"""Provider-independent model client contract."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Literal, Protocol, runtime_checkable

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


class ModelTextDelta(StrictModel):
    """Incremental provider text translated into the canonical stream."""

    type: Literal["text_delta"] = "text_delta"
    delta: str = Field(min_length=1)


class ModelCompleted(StrictModel):
    """Terminal provider event containing the complete canonical result."""

    type: Literal["completed"] = "completed"
    result: ModelResult


ModelStreamEvent = Annotated[
    ModelTextDelta | ModelCompleted,
    Field(discriminator="type"),
]


class ModelClient(Protocol):
    """Async model generation boundary."""

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult: ...


@runtime_checkable
class StreamingModelClient(Protocol):
    """Optional extension implemented by providers with token streaming."""

    def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]: ...
