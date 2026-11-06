"""Context budgets preserve mandatory messages and complete historical exchanges."""

import json
from collections.abc import Sequence

import pytest

from agent_runtime.context import ContextWindow
from agent_runtime.errors import ContextBudgetExceededError, ContextSelectionError
from agent_runtime.models import Message, TextContent, ToolCall, ToolCallContent, ToolResultContent


def message(text: str, role: str = "user") -> Message:
    return Message.model_validate({"role": role, "content": [{"type": "text", "text": text}]})


def size(messages: Sequence[Message]) -> int:
    return len(
        json.dumps(
            [item.model_dump(mode="json") for item in messages],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def call(*ids: str) -> Message:
    return Message(
        role="assistant",
        content=[ToolCallContent(tool_call=ToolCall(id=id_, name="lookup")) for id_ in ids],
    )


def result(id_: str) -> Message:
    return Message(role="tool", content=[ToolResultContent(tool_call_id=id_, result={"ok": True})])


@pytest.mark.parametrize("budget", [True, False, 0, 1, -1, 2.5, float("inf")])
def test_invalid_window_budget(budget: object) -> None:
    with pytest.raises(ValueError):
        ContextWindow(max_bytes=budget)  # type: ignore[arg-type]


def test_exact_utf8_boundary_preserves_entire_current_request() -> None:
    current = [message("你好"), message("second current message")]
    selected = ContextWindow(max_bytes=size(current)).select([], current)
    assert list(selected.messages) == current
    assert selected.selected_bytes == size(current)
    assert selected.omitted_history_messages == 0
    with pytest.raises(ContextBudgetExceededError):
        ContextWindow(max_bytes=size(current) - 1).select([], current)


def test_keeps_recent_complete_turn_and_all_system_messages_in_original_order() -> None:
    history = [
        message("policy one", "system"),
        message("old"),
        message("old answer", "assistant"),
        message("policy two", "system"),
        message("recent"),
        call("a", "b"),
        result("b"),
        result("a"),
        message("recent answer", "assistant"),
    ]
    current = [message("now")]
    expected = [history[0], *history[3:], *current]
    selected = ContextWindow(max_bytes=size(expected)).select(history, current)
    assert list(selected.messages) == expected
    assert selected.omitted_history_messages == 2
    assert selected.omitted_history_groups == 1
    assert selected.input_bytes == size([*history, *current])
    assert selected.selected_bytes == size(expected)


def test_never_fills_budget_with_older_groups_after_newer_group_does_not_fit() -> None:
    history = [message("small"), message("x" * 5000)]
    current = [message("now")]
    selected = ContextWindow(max_bytes=size([history[0], *current])).select(history, current)
    assert list(selected.messages) == current
    assert selected.omitted_history_groups == 2


def test_system_messages_can_make_mandatory_context_overflow() -> None:
    current = [message("now")]
    with pytest.raises(ContextBudgetExceededError) as error:
        ContextWindow(max_bytes=size(current)).select(
            [message("PRIVATE POLICY", "system")], current
        )
    assert "PRIVATE" not in str(error.value)
    assert error.value.code == "context_budget_exceeded"
    assert not error.value.retryable


def test_tool_exchange_crossing_current_request_is_mandatory_as_a_whole() -> None:
    history = [message("earlier"), message("execute"), call("a", "b")]
    current = [result("a"), result("b"), message("continue")]
    expected = [*history[1:], *current]
    selected = ContextWindow(max_bytes=size(expected)).select(history, current)
    assert list(selected.messages) == expected
    with pytest.raises(ContextBudgetExceededError):
        ContextWindow(max_bytes=size(current)).select(history, current)


@pytest.mark.parametrize(
    "history",
    [[call("private")], [result("private")], [call("private", "private"), result("private")]],
)
def test_malformed_tool_relationships_fail_even_when_history_would_not_fit(
    history: list[Message],
) -> None:
    current = [message("now")]
    with pytest.raises(ContextSelectionError) as error:
        ContextWindow(max_bytes=size(current)).select(history, current)
    assert "private" not in str(error.value)


def test_closed_call_ids_may_be_reused_in_a_later_turn() -> None:
    history = [
        message("first"),
        call("id"),
        result("id"),
        message("second"),
        call("id"),
        result("id"),
    ]
    current = [message("now")]
    expected = [*history[3:], *current]
    assert (
        list(ContextWindow(max_bytes=size(expected)).select(history, current).messages) == expected
    )


def test_selection_does_not_mutate_or_alias_input_messages() -> None:
    history, current = [message("past")], [message("now")]
    before = [item.model_dump(mode="json") for item in [*history, *current]]
    selected = ContextWindow(max_bytes=10000).select(history, current)
    selected.messages[0].content.append(TextContent(text="changed"))
    selected.messages[-1].content.append(TextContent(text="changed current"))
    assert [item.model_dump(mode="json") for item in [*history, *current]] == before


def test_selected_nested_tool_payload_is_detached() -> None:
    history = [message("lookup"), call("id"), result("id")]
    selected = ContextWindow(max_bytes=10000).select(history, [message("now")])
    part = selected.messages[2].content[0]
    assert isinstance(part, ToolResultContent)
    part.result["ok"] = False
    original = history[2].content[0]
    assert isinstance(original, ToolResultContent)
    assert original.result == {"ok": True}


def test_empty_lists_have_array_punctuation_cost() -> None:
    selected = ContextWindow(max_bytes=2).select([], [])
    assert selected.messages == ()
    assert selected.input_bytes == selected.selected_bytes == 2
