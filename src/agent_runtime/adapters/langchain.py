"""LangChain Runnable adapter using explicit edge mappers."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Protocol, cast

from agent_runtime.adapters.base import AgentCompleted, AgentStreamEvent
from agent_runtime.adapters.framework_streaming import (
    FrameworkStreamMapper,
    StreamingRunnable,
    checked_events,
    close_source,
    runnable_events,
)
from agent_runtime.adapters.mapping import messages_input, messages_output
from agent_runtime.errors import AgentInvocationError, RuntimeKitError
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
        streaming: bool = False,
        stream_mapper: FrameworkStreamMapper | None = None,
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
        self._streaming = streaming or stream_mapper is not None
        self._stream_mapper = stream_mapper
        if self._streaming and not callable(getattr(runnable, "astream", None)):
            raise TypeError("Incremental LangChain streaming requires astream()")

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
            if isinstance(exc, RuntimeKitError):
                raise
            raise AgentInvocationError("LangChain agent invocation failed") from exc

    async def stream(self, request: InvocationInput) -> AsyncGenerator[AgentStreamEvent, None]:
        """Opt into astream translation, preserving terminal fallback by default."""
        if not self._streaming:
            yield AgentCompleted(output=await self.invoke(request))
            return
        source: AsyncIterator[object] | None = None
        try:
            source = cast(StreamingRunnable, self._runnable).astream(self._input_mapper(request))
            events = (
                self._stream_mapper(source, request)
                if self._stream_mapper
                else runnable_events(source, request, self._output_mapper)
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
            raise AgentInvocationError("LangChain agent streaming failed") from exc
        finally:
            if source is not None:
                await close_source(source)
