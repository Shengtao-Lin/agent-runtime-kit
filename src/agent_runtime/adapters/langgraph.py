"""LangGraph adapter using explicit state mappers."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Protocol, cast

from agent_runtime.adapters.base import AgentCompleted, AgentStreamEvent
from agent_runtime.adapters.framework_streaming import (
    FrameworkStreamMapper,
    MessageFilter,
    StreamingGraph,
    checked_events,
    close_source,
    graph_events,
)
from agent_runtime.adapters.mapping import messages_input, messages_output
from agent_runtime.errors import AgentInvocationError, RuntimeKitError
from agent_runtime.models import AgentDescriptor, InvocationInput, InvocationOutput


class AsyncGraph(Protocol):
    """Minimal structural contract implemented by compiled LangGraph graphs."""

    async def ainvoke(self, input: object) -> object: ...


class LangGraphAdapter:
    """Expose a compiled LangGraph graph through `AgentInvoker`."""

    def __init__(
        self,
        *,
        agent_id: str,
        version: str,
        graph: AsyncGraph,
        state_input_mapper: Callable[[InvocationInput], object] = messages_input,
        state_output_mapper: Callable[
            [object, InvocationInput], InvocationOutput
        ] = messages_output,
        capabilities: set[str] | None = None,
        streaming: bool = False,
        stream_mapper: FrameworkStreamMapper | None = None,
        stream_message_filter: MessageFilter | None = None,
    ) -> None:
        self._descriptor = AgentDescriptor(
            agent_id=agent_id,
            version=version,
            framework="langgraph",
            capabilities=capabilities or set(),
        )
        self._graph = graph
        self._state_input_mapper = state_input_mapper
        self._state_output_mapper = state_output_mapper
        self._streaming = streaming or stream_mapper is not None
        self._stream_mapper = stream_mapper
        self._stream_message_filter = stream_message_filter
        if self._streaming and not callable(getattr(graph, "astream", None)):
            raise TypeError("Incremental LangGraph streaming requires astream()")

    @property
    def descriptor(self) -> AgentDescriptor:
        """Return the stable public agent descriptor."""
        return self._descriptor

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        """Invoke and normalize a graph, hiding framework exceptions."""
        try:
            value = await self._graph.ainvoke(self._state_input_mapper(request))
            return self._state_output_mapper(value, request)
        except Exception as exc:
            if isinstance(exc, RuntimeKitError):
                raise
            raise AgentInvocationError("LangGraph agent invocation failed") from exc

    async def stream(self, request: InvocationInput) -> AsyncGenerator[AgentStreamEvent, None]:
        """Stream v1 messages/values modes with one final canonical state."""
        if not self._streaming:
            yield AgentCompleted(output=await self.invoke(request))
            return
        source: AsyncIterator[object] | None = None
        try:
            source = cast(StreamingGraph, self._graph).astream(
                self._state_input_mapper(request), stream_mode=["messages", "values"]
            )
            events = (
                self._stream_mapper(source, request)
                if self._stream_mapper
                else graph_events(
                    source, request, self._state_output_mapper, self._stream_message_filter
                )
            )
            checked = checked_events(events)
            try:
                async for event in checked:
                    yield event
            finally:
                await close_source(checked)
        except Exception as exc:
            if isinstance(exc, RuntimeKitError):
                raise
            raise AgentInvocationError("LangGraph agent streaming failed") from exc
        finally:
            if source is not None:
                await close_source(source)
