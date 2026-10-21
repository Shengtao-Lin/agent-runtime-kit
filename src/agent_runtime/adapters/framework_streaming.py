"""Opt-in framework stream translation; framework imports stay lazy."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Protocol, cast

from agent_runtime.adapters.base import (
    AgentCompleted,
    AgentStreamEvent,
    AgentTextDelta,
    AgentToolResult,
)
from agent_runtime.models import InvocationInput, InvocationOutput

FrameworkStreamMapper = Callable[
    [AsyncIterator[object], InvocationInput], AsyncIterator[AgentStreamEvent]
]
OutputMapper = Callable[[object, InvocationInput], InvocationOutput]
MessageFilter = Callable[[Mapping[str, object]], bool]


class StreamingRunnable(Protocol):
    def astream(self, input: object) -> AsyncIterator[object]: ...


class StreamingGraph(Protocol):
    def astream(self, input: object, *, stream_mode: list[str]) -> AsyncIterator[object]: ...


async def close_source(source: AsyncIterator[object]) -> None:
    """Close framework generators deterministically when the consumer exits early."""
    close = getattr(source, "aclose", None)
    if callable(close):
        await cast(Callable[[], Awaitable[object]], close)()


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ValueError("Unsupported streamed content; provide an explicit stream mapper")
    parts: list[str] = []
    for block in cast(list[object], value):
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and cast(dict[str, object], block).get("type") == "text":
            text = cast(dict[str, object], block).get("text")
            if not isinstance(text, str):
                raise ValueError("Invalid streamed text block")
            parts.append(text)
        else:
            raise ValueError("Unsupported streamed content; provide an explicit stream mapper")
    return "".join(parts)


async def runnable_events(
    source: AsyncIterator[object], request: InvocationInput, output_mapper: OutputMapper
) -> AsyncIterator[AgentStreamEvent]:
    """Accumulate one assistant message stream or accept one terminal state value."""
    from langchain_core.messages import AIMessageChunk, message_chunk_to_message

    accumulated: AIMessageChunk | None = None
    terminal: object = None
    saw_terminal = False
    try:
        async for value in source:
            if isinstance(value, AIMessageChunk):
                if saw_terminal:
                    raise ValueError("Mixed framework stream output needs an explicit mapper")
                if (
                    accumulated is not None
                    and accumulated.id
                    and value.id
                    and value.id != accumulated.id
                ):
                    raise ValueError("Multiple message streams need an explicit mapper")
                accumulated = value if accumulated is None else accumulated + value
                text = _text(value.content)
                if text:
                    yield AgentTextDelta(delta=text)
            else:
                if accumulated is not None or saw_terminal:
                    raise ValueError("Multiple framework state values need an explicit mapper")
                terminal, saw_terminal = value, True
        if accumulated is not None:
            terminal = message_chunk_to_message(accumulated)
        elif not saw_terminal:
            raise ValueError("Framework stream ended without output")
        output = output_mapper(terminal, request)
        for result in output.tool_results:
            yield AgentToolResult(result=result)
        yield AgentCompleted(output=output)
    finally:
        await close_source(source)


async def graph_events(
    source: AsyncIterator[object],
    request: InvocationInput,
    output_mapper: OutputMapper,
    message_filter: MessageFilter | None,
) -> AsyncIterator[AgentStreamEvent]:
    """Translate v1 messages/values modes, retaining only the final graph state."""
    from langchain_core.messages import AIMessageChunk

    final_state: object = None
    saw_state = False
    try:
        async for value in source:
            if not isinstance(value, tuple) or len(cast(tuple[object, ...], value)) != 2:
                raise ValueError("Expected graph (mode, data) stream output")
            mode, data = cast(tuple[object, object], value)
            if mode == "values":
                final_state, saw_state = data, True
            elif mode == "messages":
                if not isinstance(data, tuple) or len(cast(tuple[object, ...], data)) != 2:
                    raise ValueError("Invalid graph message event")
                chunk, metadata = cast(tuple[object, object], data)
                if not isinstance(metadata, Mapping):
                    raise ValueError("Invalid graph message metadata")
                if message_filter and not message_filter(cast(Mapping[str, object], metadata)):
                    continue
                if isinstance(chunk, AIMessageChunk):
                    text = _text(chunk.content)
                    if text:
                        yield AgentTextDelta(delta=text)
            else:
                raise ValueError("Unsupported graph stream mode")
        if not saw_state:
            raise ValueError("Graph stream ended without final state")
        output = output_mapper(final_state, request)
        for result in output.tool_results:
            yield AgentToolResult(result=result)
        yield AgentCompleted(output=output)
    finally:
        await close_source(source)


async def checked_events(
    events: AsyncIterator[AgentStreamEvent],
) -> AsyncIterator[AgentStreamEvent]:
    """Require exactly one terminal event, with no later partial output."""
    completed = False
    try:
        async for event in events:
            if completed:
                raise ValueError("Framework stream emitted an event after completion")
            completed = isinstance(event, AgentCompleted)
            yield event
    finally:
        await close_source(events)
    if not completed:
        raise ValueError("Framework stream ended without completion")
