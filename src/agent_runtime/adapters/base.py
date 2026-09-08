"""Framework-neutral agent invocation boundaries."""

from collections.abc import AsyncIterator
from typing import Annotated, Literal, Protocol, runtime_checkable

from pydantic import Field

from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    StrictModel,
    ToolResult,
)


class AgentTextDelta(StrictModel):
    """Incremental text emitted by a streaming invoker."""

    type: Literal["text_delta"] = "text_delta"
    delta: str = Field(min_length=1)


class AgentToolResult(StrictModel):
    """Completed tool execution emitted by a streaming invoker."""

    type: Literal["tool_result"] = "tool_result"
    result: ToolResult


class AgentCompleted(StrictModel):
    """Terminal invoker event containing the complete canonical output."""

    type: Literal["completed"] = "completed"
    output: InvocationOutput


AgentStreamEvent = Annotated[
    AgentTextDelta | AgentToolResult | AgentCompleted,
    Field(discriminator="type"),
]


class AgentInvoker(Protocol):
    """An agent implementation callable through the shared runtime contract."""

    @property
    def descriptor(self) -> AgentDescriptor: ...

    async def invoke(self, request: InvocationInput) -> InvocationOutput: ...


@runtime_checkable
class StreamingAgentInvoker(Protocol):
    """Optional extension implemented by invokers supporting incremental output."""

    def stream(self, request: InvocationInput) -> AsyncIterator[AgentStreamEvent]: ...
