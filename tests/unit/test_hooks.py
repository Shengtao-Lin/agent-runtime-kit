from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from pydantic import BaseModel

from agent_runtime.hooks import (
    GuardrailBlockedError,
    GuardrailHook,
    HookEvent,
    HookManager,
    MessageLengthGuardrail,
    PhraseBlockGuardrail,
)
from agent_runtime.models import Message, RuntimeContext, TextContent
from agent_runtime.tools import ToolRegistry


def context() -> RuntimeContext:
    return RuntimeContext(run_id=uuid4(), thread_id=uuid4())


def empty_calls() -> list[str]:
    return []


@dataclass
class RecordingHook:
    name: str
    calls: list[str] = field(default_factory=empty_calls)

    async def handle(self, event: HookEvent) -> None:
        self.calls.append(f"{self.name}:{event.phase}")


@pytest.mark.asyncio
async def test_hooks_run_in_registration_order() -> None:
    calls: list[str] = []
    first = RecordingHook("first", calls)
    second = RecordingHook("second", calls)
    manager = HookManager([first, second])
    await manager.run(HookEvent(phase="before_model", context=context()))
    assert calls == ["first:before_model", "second:before_model"]


@pytest.mark.asyncio
async def test_phrase_and_length_guardrails_allow_and_block() -> None:
    safe = Message(role="user", content=[TextContent(text="Hello")])
    blocked = Message(role="user", content=[TextContent(text="contains forbidden phrase")])
    phrase = GuardrailHook(PhraseBlockGuardrail(["forbidden"]), phases={"before_model"})
    length = GuardrailHook(MessageLengthGuardrail(10), phases={"before_model"})

    await phrase.handle(HookEvent(phase="before_model", context=context(), messages=[safe]))
    with pytest.raises(GuardrailBlockedError) as phrase_error:
        await phrase.handle(HookEvent(phase="before_model", context=context(), messages=[blocked]))
    assert phrase_error.value.decision.reason_code == "configured_phrase"
    with pytest.raises(GuardrailBlockedError) as length_error:
        await length.handle(HookEvent(phase="before_model", context=context(), messages=[blocked]))
    assert length_error.value.decision.reason_code == "message_too_long"


class EmptyInput(BaseModel):
    pass


@pytest.mark.asyncio
async def test_tool_hooks_wrap_execution() -> None:
    calls: list[str] = []
    manager = HookManager([RecordingHook("audit", calls)])
    tools = ToolRegistry(manager)
    tools.register(
        name="ping",
        description="Return a synthetic response.",
        input_model=EmptyInput,
        handler=lambda: "pong",
    )
    result = await tools.invoke("ping", {}, tool_call_id="call-1", context=context())
    assert result.output == "pong"
    assert calls == ["audit:before_tool", "audit:after_tool"]
