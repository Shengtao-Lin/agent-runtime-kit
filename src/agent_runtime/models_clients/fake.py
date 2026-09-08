"""Deterministic model client for tests and credential-free examples."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from agent_runtime.models import Message, RuntimeContext, TextContent
from agent_runtime.models_clients.base import (
    ModelCompleted,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
)
from agent_runtime.tools.models import ToolDefinition


class DeterministicModelClient:
    """Return a finite scripted sequence and record canonical inputs."""

    def __init__(self, results: Sequence[ModelResult]) -> None:
        if not results:
            raise ValueError("At least one model result is required")
        self._results = list(results)
        self._index = 0
        self.calls: list[tuple[Message, ...]] = []

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        """Return the next result, repeating the final result if exhausted."""
        del tools, context
        self.calls.append(tuple(messages))
        result = self._results[min(self._index, len(self._results) - 1)]
        self._index += 1
        return result.model_copy(deep=True)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Emit scripted text parts and the complete deterministic result."""
        del tools, context
        self.calls.append(tuple(messages))
        result = self._results[min(self._index, len(self._results) - 1)].model_copy(deep=True)
        self._index += 1
        for part in result.message.content:
            if isinstance(part, TextContent):
                yield ModelTextDelta(delta=part.text)
        yield ModelCompleted(result=result)
