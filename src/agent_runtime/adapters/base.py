"""Framework-neutral agent invocation boundary."""

from typing import Protocol

from agent_runtime.models import AgentDescriptor, InvocationInput, InvocationOutput


class AgentInvoker(Protocol):
    """An agent implementation callable through the shared runtime contract."""

    @property
    def descriptor(self) -> AgentDescriptor: ...

    async def invoke(self, request: InvocationInput) -> InvocationOutput: ...
