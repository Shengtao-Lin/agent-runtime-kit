"""Composable demonstration guardrails implemented as lifecycle hooks."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import Field

from agent_runtime.errors import RuntimeKitError
from agent_runtime.hooks.base import HookEvent, HookPhase
from agent_runtime.models import Message, RuntimeContext, StrictModel, TextContent


class GuardrailDecision(StrictModel):
    """Explicit allow or block result returned by every guardrail."""

    allowed: bool
    reason_code: str | None = None
    public_message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class GuardrailBlockedError(RuntimeKitError):
    """Raised when a configured guardrail blocks lifecycle execution."""

    code = "guardrail_blocked"

    def __init__(self, decision: GuardrailDecision) -> None:
        super().__init__(decision.public_message or "Request blocked by runtime policy")
        self.decision = decision


class Guardrail(Protocol):
    """Evaluate canonical messages without mutating them."""

    @property
    def name(self) -> str: ...

    async def evaluate(
        self, messages: Sequence[Message], *, context: RuntimeContext
    ) -> GuardrailDecision: ...


class GuardrailHook:
    """Apply a guardrail during configured model lifecycle phases."""

    def __init__(self, guardrail: Guardrail, *, phases: set[HookPhase]) -> None:
        unsupported = phases - {"before_model", "after_model"}
        if unsupported:
            raise ValueError("Message guardrails only support before_model and after_model")
        self._guardrail = guardrail
        self._phases = phases

    @property
    def name(self) -> str:
        """Return a stable hook name derived from the guardrail."""
        return f"guardrail:{self._guardrail.name}"

    async def handle(self, event: HookEvent) -> None:
        """Evaluate configured phases and raise an explicit block error."""
        if event.phase not in self._phases:
            return
        decision = await self._guardrail.evaluate(event.messages, context=event.context)
        if not decision.allowed:
            raise GuardrailBlockedError(decision)


class PhraseBlockGuardrail:
    """Demonstration-only case-insensitive phrase blocker."""

    name = "demo_phrase_block"

    def __init__(self, phrases: Sequence[str]) -> None:
        normalized = {phrase.strip().casefold() for phrase in phrases if phrase.strip()}
        if not normalized:
            raise ValueError("At least one non-empty blocked phrase is required")
        self._phrases = normalized

    async def evaluate(
        self, messages: Sequence[Message], *, context: RuntimeContext
    ) -> GuardrailDecision:
        """Block when a text part contains a configured phrase."""
        del context
        for message in messages:
            for part in message.content:
                if isinstance(part, TextContent):
                    text = part.text.casefold()
                    if any(phrase in text for phrase in self._phrases):
                        return GuardrailDecision(
                            allowed=False,
                            reason_code="configured_phrase",
                            public_message="The message was blocked by configured policy.",
                        )
        return GuardrailDecision(allowed=True)


class MessageLengthGuardrail:
    """Demonstration-only limit on total text characters."""

    name = "demo_message_length"

    def __init__(self, max_characters: int) -> None:
        if max_characters < 1:
            raise ValueError("max_characters must be positive")
        self._max_characters = max_characters

    async def evaluate(
        self, messages: Sequence[Message], *, context: RuntimeContext
    ) -> GuardrailDecision:
        """Block message collections exceeding the configured text limit."""
        del context
        total = sum(
            len(part.text)
            for message in messages
            for part in message.content
            if isinstance(part, TextContent)
        )
        if total > self._max_characters:
            return GuardrailDecision(
                allowed=False,
                reason_code="message_too_long",
                public_message="The message exceeds the configured length limit.",
                metadata={"max_characters": self._max_characters},
            )
        return GuardrailDecision(allowed=True)
