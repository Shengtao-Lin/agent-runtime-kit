"""Explicit initial-context selection, independent of durable transcript retention."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from agent_runtime.errors import ContextBudgetExceededError, ContextSelectionError
from agent_runtime.models import Message

__all__ = ["ContextSelection", "ContextWindow"]


@dataclass(frozen=True, slots=True)
class ContextSelection:
    """Isolated selected messages and content-free accounting for one selection."""

    messages: tuple[Message, ...]
    input_bytes: int
    selected_bytes: int
    history_messages: int
    request_messages: int
    omitted_history_messages: int
    omitted_history_groups: int

    def summary(self) -> dict[str, int]:
        """Return safe counts without message bodies, identifiers or payloads."""
        return {
            "input_bytes": self.input_bytes,
            "selected_bytes": self.selected_bytes,
            "history_messages": self.history_messages,
            "request_messages": self.request_messages,
            "selected_messages": len(self.messages),
            "omitted_history_messages": self.omitted_history_messages,
            "omitted_history_groups": self.omitted_history_groups,
        }


@dataclass(frozen=True, slots=True)
class ContextWindow:
    """Keep mandatory input and recent complete history within a canonical JSON byte budget.

    This selects after full history loading. It is not a token, store-read, process-memory or
    per-model-call limit. System messages and the complete current request are mandatory.
    """

    max_bytes: int

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or self.max_bytes < 2:
            raise ValueError("max_bytes must be an integer of at least two")

    def select(self, history: Sequence[Message], current: Sequence[Message]) -> ContextSelection:
        """Select whole groups, rejecting malformed exchanges or mandatory overflow."""
        messages = [*history, *current]
        groups: list[list[int]] = []
        group: list[int] = []
        pending: set[str] = set()
        protected: set[int] = set()
        sizes: list[int] = []
        for index, message in enumerate(messages):
            sizes.append(
                len(
                    json.dumps(
                        message.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                )
            )
            if (message.role == "user" or index == len(history)) and not pending and group:
                groups.append(group)
                group = []
            if message.role == "system":
                protected.add(index)
                continue
            group.append(index)
            for part in message.content:
                if part.type == "tool_call":
                    if part.tool_call.id in pending:
                        raise ContextSelectionError("Context contains an ambiguous tool exchange")
                    pending.add(part.tool_call.id)
                elif part.type == "tool_result":
                    if part.tool_call_id not in pending:
                        raise ContextSelectionError("Context contains an unmatched tool result")
                    pending.remove(part.tool_call_id)
        if pending:
            raise ContextSelectionError("Context contains an incomplete tool exchange")
        if group:
            groups.append(group)

        selected = protected | set(range(len(history), len(messages)))
        mandatory = {index for index, group in enumerate(groups) if selected.intersection(group)}
        for index in mandatory:
            selected.update(groups[index])

        def byte_count(indices: set[int]) -> int:
            return 2 + sum(sizes[index] for index in indices) + max(0, len(indices) - 1)

        selected_bytes = byte_count(selected)
        if selected_bytes > self.max_bytes:
            raise ContextBudgetExceededError("Mandatory context exceeds the configured byte budget")
        chosen = set(mandatory)
        for index in reversed(range(len(groups))):
            if index in mandatory:
                continue
            candidate_bytes = (
                selected_bytes
                + sum(sizes[item] for item in groups[index])
                + len(groups[index])
                - (0 if selected else 1)
            )
            if candidate_bytes > self.max_bytes:
                break
            selected.update(groups[index])
            selected_bytes = candidate_bytes
            chosen.add(index)
        return ContextSelection(
            messages=tuple(messages[index].model_copy(deep=True) for index in sorted(selected)),
            input_bytes=byte_count(set(range(len(messages)))),
            selected_bytes=selected_bytes,
            history_messages=len(history),
            request_messages=len(current),
            omitted_history_messages=sum(index not in selected for index in range(len(history))),
            omitted_history_groups=len(groups) - len(chosen),
        )
