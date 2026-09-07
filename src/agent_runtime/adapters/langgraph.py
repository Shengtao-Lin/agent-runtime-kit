"""LangGraph adapter using explicit state mappers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agent_runtime.adapters.mapping import messages_input, messages_output
from agent_runtime.errors import AgentInvocationError
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
            if isinstance(exc, AgentInvocationError):
                raise
            raise AgentInvocationError("LangGraph agent invocation failed") from exc
