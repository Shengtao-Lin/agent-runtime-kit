"""Provider-independent model client contract."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field, model_validator

from agent_runtime._json import validate_json_value
from agent_runtime.models import (
    Message,
    RuntimeContext,
    StrictModel,
    ToolCall,
    ToolCallContent,
    Usage,
)
from agent_runtime.tools.models import ToolDefinition


def _empty_tool_calls() -> list[ToolCall]:
    return []


class ModelResult(StrictModel):
    """Assistant output whose ordered tool calls exactly match its message parts."""

    message: Message
    tool_calls: list[ToolCall] = Field(default_factory=_empty_tool_calls)
    usage: Usage | None = None

    @model_validator(mode="after")
    def validate_tool_calls(self) -> ModelResult:
        """Keep dispatch and recorded history unambiguous, including nested JSON types."""
        if self.message.role != "assistant":
            raise ValueError("Model result must contain an assistant message")
        message_calls = [
            part.tool_call for part in self.message.content if isinstance(part, ToolCallContent)
        ]
        if len(message_calls) != len(self.tool_calls):
            raise ValueError("Model tool calls must match assistant message parts")
        if len({call.id for call in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("Model result must have unique tool call IDs")
        for recorded, executable in zip(message_calls, self.tool_calls, strict=True):
            validate_json_value(recorded.arguments)
            validate_json_value(executable.arguments)
            if (
                recorded.id != executable.id
                or recorded.name != executable.name
                or json.dumps(recorded.arguments, sort_keys=True, allow_nan=False)
                != json.dumps(executable.arguments, sort_keys=True, allow_nan=False)
            ):
                raise ValueError("Model tool calls must match assistant message parts")
        return self


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
