"""Versioned, provider-independent runtime models."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_METADATA_KEYS = 64
MAX_METADATA_BYTES = 16_384


class StrictModel(BaseModel):
    """Base model that rejects unknown public contract fields."""

    model_config = ConfigDict(extra="forbid")


def validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Validate caller metadata as a small JSON-compatible object."""
    if len(value) > MAX_METADATA_KEYS:
        raise ValueError(f"metadata cannot contain more than {MAX_METADATA_KEYS} keys")
    for key in value:
        if not key or len(key) > 128:
            raise ValueError("metadata keys must contain between 1 and 128 characters")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON-compatible") from exc
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError(f"metadata cannot exceed {MAX_METADATA_BYTES} encoded bytes")
    return value


class MetadataModel(StrictModel):
    """Strict model carrying bounded, JSON-compatible metadata."""

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_metadata(value)


class TextContent(StrictModel):
    """A plain-text message part."""

    type: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=32_000)


class ToolCall(StrictModel):
    """A canonical request to invoke a registered tool."""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallContent(StrictModel):
    """A tool-call message part."""

    type: Literal["tool_call"] = "tool_call"
    tool_call: ToolCall


class ToolResultContent(StrictModel):
    """A message part carrying the result of a tool call."""

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str = Field(min_length=1, max_length=256)
    result: Any


ContentPart = Annotated[
    TextContent | ToolCallContent | ToolResultContent,
    Field(discriminator="type"),
]


class Message(StrictModel):
    """A canonical message exchanged by callers, agents, and tools."""

    id: UUID = Field(default_factory=uuid4)
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentPart] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_tool_content(self) -> Message:
        part_types = {part.type for part in self.content}
        if self.role == "tool" and part_types != {"tool_result"}:
            raise ValueError("tool messages may contain only tool_result parts")
        if self.role != "tool" and "tool_result" in part_types:
            raise ValueError("tool_result parts require the tool role")
        if self.role != "assistant" and "tool_call" in part_types:
            raise ValueError("tool_call parts require the assistant role")
        return self


class Usage(StrictModel):
    """Provider-neutral token usage."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ToolResult(StrictModel):
    """A structured tool execution result."""

    id: UUID = Field(default_factory=uuid4)
    tool_call_id: str
    tool_name: str
    status: Literal["succeeded", "failed"]
    output: Any = None
    error_code: str | None = None


def _empty_tool_results() -> list[ToolResult]:
    return []


class AgentDescriptor(StrictModel):
    """Discoverable agent identity and capabilities."""

    agent_id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
    version: str = Field(min_length=1, max_length=64)
    framework: Literal["native", "langchain", "langgraph"]
    capabilities: set[str] = Field(default_factory=set)


class RuntimeContext(StrictModel):
    """Per-invocation identifiers and trusted runtime attributes."""

    run_id: UUID
    thread_id: UUID
    user_id: str | None = Field(default=None, max_length=256)
    attributes: dict[str, Any] = Field(default_factory=dict)


class RuntimeRequest(MetadataModel):
    """The versioned external invocation request."""

    contract_version: Literal["v1"] = "v1"
    request_id: UUID = Field(default_factory=uuid4)
    thread_id: UUID | None = None
    user_id: str | None = Field(default=None, max_length=256)
    messages: list[Message] = Field(min_length=1, max_length=100)


class InvocationInput(MetadataModel):
    """Canonical input passed from the runtime to an agent invoker."""

    context: RuntimeContext
    messages: list[Message]


class InvocationOutput(MetadataModel):
    """Canonical output returned by every agent implementation."""

    messages: list[Message] = Field(min_length=1)
    tool_results: list[ToolResult] = Field(default_factory=_empty_tool_results)
    usage: Usage | None = None


class RuntimeResponse(MetadataModel):
    """The versioned external invocation response."""

    contract_version: Literal["v1"] = "v1"
    run_id: UUID
    request_id: UUID
    thread_id: UUID
    agent: AgentDescriptor
    message: Message
    tool_results: list[ToolResult] = Field(default_factory=_empty_tool_results)
    usage: Usage | None = None
    trace_id: str | None = None
