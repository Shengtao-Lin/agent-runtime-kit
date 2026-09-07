"""Provider-independent model client contract."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import Field

from agent_runtime.models import Message, RuntimeContext, StrictModel, ToolCall, Usage
from agent_runtime.tools.models import ToolDefinition


def _empty_tool_calls() -> list[ToolCall]:
    return []


class ModelResult(StrictModel):
    """Canonical output from a model provider."""

    message: Message
    tool_calls: list[ToolCall] = Field(default_factory=_empty_tool_calls)
    usage: Usage | None = None


class ModelClient(Protocol):
    """Async model generation boundary."""

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult: ...
