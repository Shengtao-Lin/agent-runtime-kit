"""Ordered lifecycle hooks for runtime and tool policy."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from agent_runtime.models import Message, RuntimeContext, StrictModel, ToolResult

HookPhase = Literal["before_model", "after_model", "before_tool", "after_tool"]


def _empty_messages() -> list[Message]:
    return []


class HookEvent(StrictModel):
    """Canonical, phase-specific data delivered to lifecycle hooks."""

    phase: HookPhase
    context: RuntimeContext
    messages: list[Message] = Field(default_factory=_empty_messages)
    tool_name: str | None = None
    tool_call_id: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_result: ToolResult | None = None


class LifecycleHook(Protocol):
    """One asynchronous runtime policy extension."""

    @property
    def name(self) -> str: ...

    async def handle(self, event: HookEvent) -> None: ...


class HookManager:
    """Execute lifecycle hooks in deterministic registration order."""

    def __init__(self, hooks: list[LifecycleHook] | None = None) -> None:
        self._hooks = list(hooks or [])

    def register(self, hook: LifecycleHook) -> None:
        """Append a lifecycle hook to the ordered chain."""
        self._hooks.append(hook)

    async def run(self, event: HookEvent) -> None:
        """Execute all hooks sequentially, stopping on the first failure."""
        for hook in self._hooks:
            await hook.handle(event)
