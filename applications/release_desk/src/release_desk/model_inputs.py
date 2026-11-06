"""Opt-in host policy at the canonical model boundary; not a provider/token limit."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from agent_runtime.errors import RuntimeKitError
from agent_runtime.models import Message, RuntimeContext
from agent_runtime.models_clients.base import (
    ModelClient,
    ModelResult,
    ModelStreamEvent,
    StreamingModelClient,
)
from agent_runtime.tools.models import ToolDefinition


class ModelInputExceeded(RuntimeKitError):
    """Host canonical input budget exceeded; never retry tool work automatically."""

    code = "host_model_input_exceeded"


class ModelInputInvalid(RuntimeKitError):
    """Host input cannot be measured as finite UTF-8 JSON."""

    code = "host_model_input_invalid"


def canonical_size(values: Sequence[Message] | Sequence[ToolDefinition]) -> int:
    """Measure all canonical fields, including IDs, without provider translation."""
    try:
        return len(
            json.dumps(
                [value.model_dump(mode="json") for value in values],
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
    except (ValueError, TypeError, UnicodeError):
        raise ModelInputInvalid("Model input cannot be measured") from None


@dataclass(frozen=True)
class ModelInputBudget:
    """Independent complete-list byte budgets; equality is admitted."""

    message_bytes: int
    tool_bytes: int

    def __post_init__(self) -> None:
        for value in (self.message_bytes, self.tool_bytes):
            if type(value) is not int or value < 2:
                raise ValueError("Model input budgets must be integers of at least two bytes")

    def check(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> None:
        for dimension, values, limit in (
            ("messages", messages, self.message_bytes),
            ("tools", tools, self.tool_bytes),
        ):
            measured = canonical_size(values)
            if measured > limit:
                raise ModelInputExceeded(
                    "Model input exceeds the host budget",
                    internal_context={"dimension": dimension, "bytes": measured, "limit": limit},
                )


class BudgetedModel:
    """Generate-only wrapper, preserving native's generate-only stream fallback."""

    def __init__(self, delegate: ModelClient, budget: ModelInputBudget) -> None:
        self._delegate = delegate
        self._budget = budget

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        self._budget.check(messages, tools)
        return await self._delegate.generate(messages, tools=tools, context=context)


class StreamingBudgetedModel(BudgetedModel):
    """Check immediately before opening a delegate stream and own its closure."""

    def __init__(
        self, delegate: ModelClient, stream_delegate: StreamingModelClient, budget: ModelInputBudget
    ) -> None:
        super().__init__(delegate, budget)
        self._stream_delegate = stream_delegate

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        self._budget.check(messages, tools)
        source = self._stream_delegate.stream(messages, tools=tools, context=context)
        try:
            async for event in source:
                yield event
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()


def budget_model(delegate: ModelClient, budget: ModelInputBudget) -> ModelClient:
    """Explicitly protect this delegate; other framework models remain host-owned."""
    if isinstance(delegate, StreamingModelClient):
        return StreamingBudgetedModel(delegate, delegate, budget)
    return BudgetedModel(delegate, budget)
