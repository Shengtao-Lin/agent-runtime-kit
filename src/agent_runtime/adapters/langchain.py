"""LangChain Runnable adapter using explicit edge mappers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agent_runtime.adapters.mapping import messages_input, messages_output
from agent_runtime.errors import AgentInvocationError
from agent_runtime.models import AgentDescriptor, InvocationInput, InvocationOutput


class AsyncRunnable(Protocol):
    """Minimal structural contract implemented by LangChain runnables."""

    async def ainvoke(self, input: object) -> object: ...


class LangChainRunnableAdapter:
    """Expose a LangChain-compatible runnable through `AgentInvoker`."""

    def __init__(
        self,
        *,
        agent_id: str,
        version: str,
        runnable: AsyncRunnable,
        input_mapper: Callable[[InvocationInput], object] = messages_input,
        output_mapper: Callable[[object, InvocationInput], InvocationOutput] = messages_output,
        capabilities: set[str] | None = None,
    ) -> None:
        self._descriptor = AgentDescriptor(
            agent_id=agent_id,
            version=version,
            framework="langchain",
            capabilities=capabilities or set(),
        )
        self._runnable = runnable
        self._input_mapper = input_mapper
        self._output_mapper = output_mapper

    @property
    def descriptor(self) -> AgentDescriptor:
        """Return the stable public agent descriptor."""
        return self._descriptor

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        """Invoke and normalize a runnable, hiding framework exceptions."""
        try:
            value = await self._runnable.ainvoke(self._input_mapper(request))
            return self._output_mapper(value, request)
        except Exception as exc:
            if isinstance(exc, AgentInvocationError):
                raise
            raise AgentInvocationError("LangChain agent invocation failed") from exc
