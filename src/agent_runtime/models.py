"""Versioned, provider-independent runtime models."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown public contract fields."""

    model_config = ConfigDict(extra="forbid")


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
        if self.role == "tool" and not any(part.type == "tool_result" for part in self.content):
            raise ValueError("tool messages must contain a tool_result part")
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


class RuntimeRequest(StrictModel):
    """The versioned external invocation request."""

    contract_version: Literal["v1"] = "v1"
    request_id: UUID = Field(default_factory=uuid4)
    thread_id: UUID | None = None
    user_id: str | None = Field(default=None, max_length=256)
    messages: list[Message] = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvocationInput(StrictModel):
    """Canonical input passed from the runtime to an agent invoker."""

    context: RuntimeContext
    messages: list[Message]
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvocationOutput(StrictModel):
    """Canonical output returned by every agent implementation."""

    messages: list[Message] = Field(min_length=1)
    tool_results: list[ToolResult] = Field(default_factory=_empty_tool_results)
    usage: Usage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RuntimeResponse(StrictModel):
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
    metadata: dict[str, Any] = Field(default_factory=dict)
