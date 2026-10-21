"""Installed-wheel incremental framework streaming and durable failure semantics."""

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from consumer_app import request
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk, message_chunk_to_message
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import create_async_engine
from typing_extensions import TypedDict

from agent_runtime import AgentRegistry, AgentRuntime
from agent_runtime.adapters import LangChainRunnableAdapter, LangGraphAdapter
from agent_runtime.adapters.framework_messages import framework_input
from agent_runtime.errors import AgentInvocationError
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import TextContent
from agent_runtime.runs import PostgresRunStore

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        "streaming" not in inspect.signature(LangChainRunnableAdapter).parameters,
        reason="Incremental framework streaming requires the v0.3 candidate",
    ),
]


class State(TypedDict):
    messages: list[object]


@asynccontextmanager
async def streaming_app(database_url, identity, framework, *, fail=False):
    engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
    memory, runs = PostgresMemoryStore(engine), PostgresRunStore(engine)
    closed, gate = asyncio.Event(), asyncio.Event()
    calls = []

    async def tokens(value: object) -> AsyncIterator[AIMessageChunk]:
        del value
        calls.append(1)
        message_id = str(uuid4())
        try:
            yield AIMessageChunk(content="Ready", id=message_id)
            await gate.wait()
            if fail:
                raise ValueError("private-incremental-failure")
            yield AIMessageChunk(content=" now", id=message_id)
        finally:
            closed.set()

    model = FakeListChatModel(responses=["Ready now"], sleep=0.001)

    async def node(state: State) -> State:
        calls.append(1)
        accumulated = None
        try:
            async for chunk in model.astream("synthetic"):
                accumulated = chunk if accumulated is None else accumulated + chunk
                await gate.wait()
                if fail:
                    raise ValueError("private-incremental-failure")
            return {"messages": [*state["messages"], message_chunk_to_message(accumulated)]}
        finally:
            closed.set()

    if framework == "langchain":
        adapter = LangChainRunnableAdapter(
            agent_id=identity,
            version="1",
            runnable=RunnableLambda(tokens),
            streaming=True,
        )
    else:
        builder = StateGraph(State)
        builder.add_node("answer", node)
        builder.add_edge(START, "answer")
        builder.add_edge("answer", END)
        adapter = LangGraphAdapter(
            agent_id=identity,
            version="1",
            graph=builder.compile(),
            state_input_mapper=framework_input,
            streaming=True,
            stream_message_filter=lambda metadata: metadata.get("langgraph_node") == "answer",
        )
    agents = AgentRegistry()
    agents.register(adapter)
    runtime = AgentRuntime(agents=agents, memory=memory, runs=runs, namespace=identity)
    try:
        yield runtime, memory, runs, closed, gate, calls
    finally:
        try:
            assert await runtime.shutdown(timeout_seconds=6)
        finally:
            await engine.dispose()


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
async def test_incremental_stream_commits_and_replays(database_url, identity, framework):
    async with streaming_app(database_url, identity, framework) as (
        runtime,
        memory,
        runs,
        closed,
        gate,
        calls,
    ):
        stream = runtime.stream(identity, request("hello"), idempotency_key="stream")
        assert (await anext(stream)).type == "started"
        async with asyncio.timeout(5):
            first = await anext(stream)
        assert first.type == "text_delta" and first.delta
        assert not closed.is_set()
        gate.set()
        remaining = [event async for event in stream]
        terminal = remaining[-1]
        assert terminal.type == "completed"
        assert (
            first.delta + "".join(e.delta for e in remaining if e.type == "text_delta")
            == "Ready now"
        )
        assert terminal.response.message.content == [TextContent(text="Ready now")]
        assert (await runs.get(terminal.response.run_id)).status == "succeeded"
        assert len(await memory.get_messages(terminal.response.thread_id, namespace=identity)) == 2
        replay = [
            e async for e in runtime.stream(identity, request("hello"), idempotency_key="stream")
        ]
        assert [e.type for e in replay] == ["started", "completed"]
        assert replay[-1] == terminal and calls == [1]


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
@pytest.mark.parametrize("failure", ["close", "error"])
async def test_partial_stream_failure_does_not_commit(database_url, identity, framework, failure):
    async with streaming_app(database_url, identity, framework, fail=True) as (
        runtime,
        memory,
        runs,
        closed,
        gate,
        _calls,
    ):
        stream = runtime.stream(identity, request("hello"), idempotency_key="partial")
        assert (await anext(stream)).type == "started"
        async with asyncio.timeout(5):
            assert (await anext(stream)).type == "text_delta"
        if failure == "close":
            await stream.aclose()
        else:
            gate.set()
            with pytest.raises(AgentInvocationError):
                _ = [e async for e in stream]
        assert closed.is_set()
        record = await runs.get_by_idempotency(agent_id=identity, idempotency_key="partial")
        assert record.status == "failed" and record.response is None
        assert await memory.get_messages(record.thread_id, namespace=identity) == ()
