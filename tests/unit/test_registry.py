from dataclasses import dataclass

import pytest

from agent_runtime.errors import DuplicateRegistrationError, UnknownAgentError
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    Message,
    TextContent,
)
from agent_runtime.registry import AgentRegistry


@dataclass
class FakeInvoker:
    descriptor: AgentDescriptor

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        del request
        return InvocationOutput(
            messages=[Message(role="assistant", content=[TextContent(text="Done")])]
        )


def test_registry_resolves_and_lists_agents_stably() -> None:
    registry = AgentRegistry()
    second = FakeInvoker(AgentDescriptor(agent_id="zeta", version="1", framework="native"))
    first = FakeInvoker(AgentDescriptor(agent_id="alpha", version="1", framework="langgraph"))
    registry.register(second)
    registry.register(first)

    assert registry.get("alpha") is first
    assert [item.agent_id for item in registry.list_descriptors()] == ["alpha", "zeta"]


def test_registry_rejects_duplicates_and_unknown_agents() -> None:
    registry = AgentRegistry()
    invoker = FakeInvoker(AgentDescriptor(agent_id="demo", version="1", framework="native"))
    registry.register(invoker)

    with pytest.raises(DuplicateRegistrationError):
        registry.register(invoker)
    with pytest.raises(UnknownAgentError):
        registry.get("missing")
