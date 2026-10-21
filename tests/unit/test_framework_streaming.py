"""Incremental translation, terminal integrity and deterministic stream teardown."""

# Framework graph builders expose partially typed generic overloads.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import cast
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agent_runtime.adapters import LangChainRunnableAdapter, LangGraphAdapter
from agent_runtime.adapters.base import AgentCompleted, AgentStreamEvent
from agent_runtime.adapters.framework_messages import framework_input
from agent_runtime.adapters.langchain import AsyncRunnable
from agent_runtime.adapters.langgraph import AsyncGraph
from agent_runtime.errors import AgentInvocationError
from agent_runtime.models import InvocationInput, Message, RuntimeContext, TextContent


def request() -> InvocationInput:
    return InvocationInput(
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        messages=[Message(role="user", content=[TextContent(text="hello")])],
    )


@pytest.mark.asyncio
async def test_real_runnable_emits_before_generator_finishes_and_aggregates() -> None:
    released = asyncio.Event()
    closed = asyncio.Event()

    async def generate(value: object) -> AsyncIterator[AIMessageChunk]:
        del value
        try:
            yield AIMessageChunk(content="Hello", id="stable")
            await released.wait()
            yield AIMessageChunk(
                content=" world",
                id="stable",
                usage_metadata={"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            )
        finally:
            closed.set()

    adapter = LangChainRunnableAdapter(
        agent_id="stream",
        version="1",
        runnable=cast(AsyncRunnable, RunnableLambda(generate)),
        streaming=True,
    )
    stream = adapter.stream(request())
    first = await anext(stream)
    assert first.type == "text_delta" and first.delta == "Hello"
    assert not closed.is_set()
    released.set()
    remaining = [event async for event in stream]
    terminal = remaining[-1]
    assert isinstance(terminal, AgentCompleted)
    assert terminal.output.messages[0].content == [TextContent(text="Hello world")]
    assert terminal.output.usage is not None and terminal.output.usage.output_tokens == 3
    assert closed.is_set()


@pytest.mark.asyncio
async def test_partial_failure_and_close_preserve_teardown() -> None:
    closed = asyncio.Event()

    async def generate(value: object) -> AsyncIterator[AIMessageChunk]:
        del value
        try:
            yield AIMessageChunk(content="partial", id="stable")
            raise ValueError("private-stream-failure")
        finally:
            closed.set()

    adapter = LangChainRunnableAdapter(
        agent_id="stream",
        version="1",
        runnable=cast(AsyncRunnable, RunnableLambda(generate)),
        streaming=True,
    )
    stream = adapter.stream(request())
    assert (await anext(stream)).type == "text_delta"
    with pytest.raises(AgentInvocationError) as caught:
        await anext(stream)
    assert "private-stream-failure" not in str(caught.value)
    assert closed.is_set()


@pytest.mark.asyncio
async def test_custom_mapper_cannot_emit_after_completion() -> None:
    async def generate(value: object) -> AsyncIterator[AIMessageChunk]:
        del value
        yield AIMessageChunk(content="one")

    async def malformed(
        source: AsyncIterator[object], invocation: InvocationInput
    ) -> AsyncIterator[AgentStreamEvent]:
        del source
        from agent_runtime.models import InvocationOutput

        output = InvocationOutput(messages=invocation.messages)
        yield AgentCompleted(output=output)
        yield AgentCompleted(output=output)

    adapter = LangChainRunnableAdapter(
        agent_id="stream",
        version="1",
        runnable=cast(AsyncRunnable, RunnableLambda(generate)),
        stream_mapper=malformed,
    )
    with pytest.raises(AgentInvocationError):
        _ = [event async for event in adapter.stream(request())]


class State(TypedDict):
    messages: list[object]


@pytest.mark.asyncio
async def test_real_graph_uses_final_values_and_filters_private_model_tokens() -> None:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import message_chunk_to_message

    model = FakeListChatModel(responses=["hidden", "visible"])

    async def node(state: State) -> State:
        accumulated: AIMessageChunk | None = None
        async for chunk in model.astream("synthetic"):
            assert isinstance(chunk, AIMessageChunk)
            accumulated = chunk if accumulated is None else accumulated + chunk
        assert accumulated is not None
        return {"messages": [*state["messages"], message_chunk_to_message(accumulated)]}

    graph = StateGraph(State)
    graph.add_node("private", node)
    graph.add_node("public", node)
    graph.add_edge(START, "private")
    graph.add_edge("private", "public")
    graph.add_edge("public", END)
    adapter = LangGraphAdapter(
        agent_id="graph",
        version="1",
        graph=cast(AsyncGraph, graph.compile()),
        state_input_mapper=framework_input,
        streaming=True,
        stream_message_filter=lambda metadata: metadata.get("langgraph_node") == "public",
    )
    events = [event async for event in adapter.stream(request())]
    assert "".join(e.delta for e in events if e.type == "text_delta") == "visible"
    terminal = events[-1]
    assert isinstance(terminal, AgentCompleted)
    assert len(terminal.output.messages) == 2
    assert terminal.output.messages[-1].content == [TextContent(text="visible")]


@pytest.mark.asyncio
async def test_graph_rejects_missing_final_state() -> None:
    class BrokenGraph:
        async def ainvoke(self, input: object) -> object:
            return input

        async def astream(self, input: object, *, stream_mode: list[str]) -> AsyncIterator[object]:
            del input, stream_mode
            yield ("messages", (AIMessageChunk(content="partial"), {}))

    adapter = LangGraphAdapter(agent_id="broken", version="1", graph=BrokenGraph(), streaming=True)
    stream = adapter.stream(request())
    assert (await anext(stream)).type == "text_delta"
    with pytest.raises(AgentInvocationError):
        await anext(stream)


@pytest.mark.asyncio
async def test_fragmented_tool_arguments_are_normalized_only_after_accumulation() -> None:
    async def generate(value: object) -> AsyncIterator[AIMessageChunk]:
        del value
        yield AIMessageChunk(
            content="",
            id="tool-message",
            tool_call_chunks=[{"name": "quote", "args": '{"units":', "id": "call", "index": 0}],
        )
        yield AIMessageChunk(
            content="",
            id="tool-message",
            tool_call_chunks=[{"name": None, "args": "3}", "id": None, "index": 0}],
        )

    adapter = LangChainRunnableAdapter(
        agent_id="tool-stream",
        version="1",
        runnable=cast(AsyncRunnable, RunnableLambda(generate)),
        streaming=True,
    )
    events = [event async for event in adapter.stream(request())]
    assert len(events) == 1
    terminal = events[0]
    assert isinstance(terminal, AgentCompleted)
    tool = terminal.output.messages[0].content[0]
    assert tool.type == "tool_call" and tool.tool_call.arguments == {"units": 3}


@pytest.mark.asyncio
async def test_abandoned_runnable_stream_closes_the_underlying_generator() -> None:
    closed = asyncio.Event()

    async def generate(value: object) -> AsyncIterator[AIMessageChunk]:
        del value
        try:
            yield AIMessageChunk(content="partial")
            await asyncio.Event().wait()
        finally:
            closed.set()

    adapter = LangChainRunnableAdapter(
        agent_id="close",
        version="1",
        runnable=cast(AsyncRunnable, RunnableLambda(generate)),
        streaming=True,
    )
    stream = adapter.stream(request())
    assert (await anext(stream)).type == "text_delta"
    await stream.aclose()
    # RunnableLambda schedules closure of its inner generator on the event loop.
    async with asyncio.timeout(1):
        await closed.wait()
