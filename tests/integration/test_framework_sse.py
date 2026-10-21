"""HTTP SSE and durable replay over real framework incremental adapters."""

# Framework graph builders expose partially typed overloads.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import create_async_engine
from typing_extensions import TypedDict

from agent_runtime import AgentRegistry, AgentRuntime
from agent_runtime.adapters import LangChainRunnableAdapter, LangGraphAdapter
from agent_runtime.adapters.framework_messages import framework_input
from agent_runtime.adapters.langchain import AsyncRunnable
from agent_runtime.adapters.langgraph import AsyncGraph
from agent_runtime.feedback import PostgresFeedbackStore
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.runs import PostgresRunStore
from agent_runtime.service import create_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class State(TypedDict):
    messages: list[BaseMessage]


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
async def test_incremental_sse_completes_after_commit_and_replays(framework: str) -> None:
    url = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    memory, runs = PostgresMemoryStore(engine), PostgresRunStore(engine)
    identity = f"sse-{uuid4()}"
    calls: list[int] = []

    async def generate(value: object) -> AsyncIterator[AIMessageChunk]:
        del value
        calls.append(1)
        message_id = str(uuid4())
        yield AIMessageChunk(content="Hello", id=message_id)
        yield AIMessageChunk(content=" SSE", id=message_id)

    if framework == "langchain":
        adapter = LangChainRunnableAdapter(
            agent_id=identity,
            version="1",
            runnable=cast(AsyncRunnable, RunnableLambda(generate)),
            streaming=True,
        )
    else:
        from langchain_core.language_models.fake_chat_models import FakeListChatModel
        from langchain_core.messages import message_chunk_to_message

        model = FakeListChatModel(responses=["Hello SSE"])

        async def node(state: State) -> State:
            calls.append(1)
            total: AIMessageChunk | None = None
            async for chunk in model.astream("synthetic"):
                assert isinstance(chunk, AIMessageChunk)
                total = chunk if total is None else total + chunk
            assert total is not None
            return {"messages": [*state["messages"], message_chunk_to_message(total)]}

        builder = StateGraph(State)
        builder.add_node("answer", node)
        builder.add_edge(START, "answer")
        builder.add_edge("answer", END)
        adapter = LangGraphAdapter(
            agent_id=identity,
            version="1",
            graph=cast(AsyncGraph, builder.compile()),
            state_input_mapper=framework_input,
            streaming=True,
        )
    agents = AgentRegistry()
    agents.register(adapter)
    runtime = AgentRuntime(agents=agents, memory=memory, runs=runs, namespace=identity)
    app = create_app(
        runtime=runtime, agents=agents, feedback=PostgresFeedbackStore(engine), readiness=memory
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
            response = await client.post(
                f"/v1/agents/{identity}/invoke/stream",
                json=payload,
                headers={"Idempotency-Key": "sse"},
            )
            assert response.status_code == 200
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            assert events[0]["type"] == "started" and events[-1]["type"] == "completed"
            assert "".join(e["delta"] for e in events if e["type"] == "text_delta") == "Hello SSE"
            terminal = events[-1]["response"]
            record = await runs.get(UUID(terminal["run_id"]))
            assert record is not None and record.status == "succeeded"
            replay = await client.post(
                f"/v1/agents/{identity}/invoke/stream",
                json=payload,
                headers={"Idempotency-Key": "sse"},
            )
            replay_events = [
                json.loads(line[6:])
                for line in replay.text.splitlines()
                if line.startswith("data: ")
            ]
            assert [e["type"] for e in replay_events] == ["started", "completed"]
            assert replay_events[-1] == events[-1] and calls == [1]
    finally:
        assert await runtime.shutdown(timeout_seconds=6)
        await engine.dispose()
