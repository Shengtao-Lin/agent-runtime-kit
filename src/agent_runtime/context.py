"""Explicit initial-context selection, independent of durable transcript retention."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import AsyncIterable, Sequence
from dataclasses import dataclass, field

from agent_runtime._streams import close_source
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

    Selection scans complete history, either materialized or incrementally consumed. This is not
    a token, store-read, process-memory or per-model-call limit. System messages and the complete
    current request are mandatory.
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

    async def select_stream(
        self, history: AsyncIterable[Message], current: Sequence[Message]
    ) -> ContextSelection:
        """Scan all history while retaining only potentially selected complete groups.

        Close the acquired iterator on completion, failure or cancellation. This limits retained
        selection payloads, not database scan/transfer or arbitrary individual-message/tool state.
        """
        selection = _StreamingSelection(self.max_bytes)
        source = history.__aiter__()
        try:
            async for message in source:
                selection.add(message, current=False)
        finally:
            await close_source(source)
        for message in current:
            selection.add(message, current=True)
        return selection.finish()


@dataclass
class _HistoryGroup:
    entries: list[tuple[int, Message]] = field(default_factory=lambda: list[tuple[int, Message]]())
    cost: int = 0
    count: int = 0
    history_count: int = 0
    mandatory: bool = False


class _StreamingSelection:
    """Keep one open group, mandatory payloads and a suffix of complete optional groups."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.group = _HistoryGroup()
        self.optional: deque[_HistoryGroup] = deque()
        self.optional_cost = 0
        self.required: list[tuple[int, Message]] = []
        self.required_cost = 0
        self.required_history = 0
        self.required_overflow = False
        self.mandatory_groups = 0
        self.groups = 0
        self.pending: set[str] = set()
        self.history_count = 0
        self.current_count = 0
        self.total_cost = 0
        self.failure: Exception | None = None

    def _fits(self, cost: int) -> bool:
        return (cost + 1 if cost else 2) <= self.budget

    def _trim(self) -> None:
        while self.optional and not self._fits(self.required_cost + self.optional_cost):
            self.optional_cost -= self.optional.popleft().cost

    def _close_group(self) -> None:
        group = self.group
        if not group.count:
            return
        self.groups += 1
        self.group = _HistoryGroup()
        if group.mandatory:
            self.mandatory_groups += 1
            self.required_cost += group.cost
            self.required_history += group.history_count
            if self._fits(self.required_cost):
                self.required.extend(group.entries)
            else:
                self.required_overflow = True
            self._trim()
        elif not self._fits(self.required_cost + group.cost):
            # An oversized newer group is a suffix barrier, not permission to fill with older ones.
            self.optional.clear()
            self.optional_cost = 0
        else:
            self.optional.append(group)
            self.optional_cost += group.cost
            self._trim()

    def add(self, message: Message, *, current: bool) -> None:
        if self.failure is not None:
            # Still consume/convert the store's remaining rows. A later canonical read failure
            # must not be hidden by an earlier relationship error in omitted history.
            return
        try:
            cost = 1 + len(
                json.dumps(
                    message.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
        except (ValueError, TypeError) as exc:
            self.failure = exc
            return
        index = self.history_count + self.current_count
        boundary = current and not self.current_count
        if (message.role == "user" or boundary) and not self.pending:
            self._close_group()
        self.total_cost += cost
        if current:
            self.current_count += 1
        else:
            self.history_count += 1
        if message.role == "system":
            self.required_cost += cost
            self.required_history += not current
            if self._fits(self.required_cost):
                self.required.append((index, message))
            else:
                self.required_overflow = True
            self._trim()
            return
        group = self.group
        group.cost += cost
        group.count += 1
        group.history_count += not current
        group.mandatory |= current
        if self._fits(group.cost):
            group.entries.append((index, message))
        else:
            # Keep relationship/cost state even when the payload cannot ever be selected.
            group.entries.clear()
        for part in message.content:
            if part.type == "tool_call":
                if part.tool_call.id in self.pending:
                    self.failure = ContextSelectionError(
                        "Context contains an ambiguous tool exchange"
                    )
                    return
                self.pending.add(part.tool_call.id)
            elif part.type == "tool_result":
                if part.tool_call_id not in self.pending:
                    self.failure = ContextSelectionError(
                        "Context contains an unmatched tool result"
                    )
                    return
                self.pending.remove(part.tool_call_id)

    def finish(self) -> ContextSelection:
        if self.failure is not None:
            raise self.failure
        if self.pending:
            raise ContextSelectionError("Context contains an incomplete tool exchange")
        self._close_group()
        if self.required_overflow:
            raise ContextBudgetExceededError("Mandatory context exceeds the configured byte budget")
        entries = self.required + [entry for group in self.optional for entry in group.entries]
        entries.sort(key=lambda entry: entry[0])
        cost = self.required_cost + self.optional_cost
        return ContextSelection(
            messages=tuple(message.model_copy(deep=True) for _, message in entries),
            input_bytes=self.total_cost + 1 if self.total_cost else 2,
            selected_bytes=cost + 1 if cost else 2,
            history_messages=self.history_count,
            request_messages=self.current_count,
            omitted_history_messages=self.history_count
            - self.required_history
            - sum(group.history_count for group in self.optional),
            omitted_history_groups=self.groups - self.mandatory_groups - len(self.optional),
        )
