"""Differential and lifecycle checks against the unchanged v0.6.0 materialized selector."""

import asyncio
import random
from collections.abc import AsyncGenerator

import pytest

from agent_runtime.context import ContextWindow
from agent_runtime.models import Message, TextContent, ToolCall, ToolCallContent, ToolResultContent


def text(value: str, role: str = "user") -> Message:
    return Message.model_validate({"role": role, "content": [{"type": "text", "text": value}]})


def call(key: str) -> Message:
    return Message(
        role="assistant", content=[ToolCallContent(tool_call=ToolCall(id=key, name="tool"))]
    )


def result(key: str) -> Message:
    return Message(role="tool", content=[ToolResultContent(tool_call_id=key, result={"ok": True})])


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(30))
async def test_stream_selection_matches_complete_materialized_contract(seed: int) -> None:
    rng = random.Random(seed)
    messages: list[Message] = []
    for turn in range(12):
        if rng.random() < 0.4:
            messages.append(text(f"system-{turn}", "system"))
        messages.append(text("界" * rng.randrange(1, 1000)))
        if rng.random() < 0.7:
            messages.extend([call(str(turn)), text("policy", "system"), result(str(turn))])
        messages.append(text("answer", "assistant"))
    # Split anywhere, including inside a tool group and before multiple current messages.
    split = rng.randrange(len(messages) + 1)
    history, current = messages[:split], messages[split:]
    for budget in (2, 200, 2000, 8000, 65536):
        closed = False

        async def source() -> AsyncGenerator[Message, None]:
            nonlocal closed
            try:
                for message in history:
                    yield message
            finally:
                closed = True

        window = ContextWindow(budget)
        try:
            expected = window.select(history, current)
        except Exception as expected_error:
            with pytest.raises(type(expected_error), match=str(expected_error)):
                await window.select_stream(source(), current)
        else:
            actual = await window.select_stream(source(), current)
            assert actual == expected
            for selected in actual.messages:
                assert all(selected is not original for original in messages)
        assert closed


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["orphan", "duplicate", "incomplete"])
async def test_omitted_malformed_history_is_still_consumed_and_rejected(defect: str) -> None:
    malformed = {
        "orphan": [result("missing")],
        "duplicate": [call("same"), call("same"), result("same")],
        "incomplete": [call("missing")],
    }[defect]
    history = [*malformed, *[text("old" * 1000) for _ in range(40)]]
    current = [text("now")]
    seen = 0

    async def source() -> AsyncGenerator[Message, None]:
        nonlocal seen
        for message in history:
            seen += 1
            yield message

    window = ContextWindow(1000)
    with pytest.raises(Exception) as expected:
        window.select(history, current)
    with pytest.raises(type(expected.value), match=str(expected.value)):
        await window.select_stream(source(), current)
    assert seen == len(history)


@pytest.mark.asyncio
async def test_stream_read_failure_precedes_deferred_relationship_error() -> None:
    async def source() -> AsyncGenerator[Message, None]:
        yield result("orphan")
        raise ValueError("synthetic canonical row failure")

    with pytest.raises(ValueError, match="canonical row failure"):
        await ContextWindow(1000).select_stream(source(), [text("now")])


@pytest.mark.asyncio
async def test_cancellation_closes_the_owned_history_iterator() -> None:
    started, closed = asyncio.Event(), asyncio.Event()

    async def source() -> AsyncGenerator[Message, None]:
        try:
            yield text("old")
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    task = asyncio.create_task(ContextWindow(1000).select_stream(source(), [text("now")]))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
async def test_oversized_optional_group_is_a_barrier_and_tool_ids_can_be_reused() -> None:
    history = [
        text("policy", "system"),
        text("old small"),
        call("reused"),
        result("reused"),
        text("huge" * 7000),
        call("reused"),
        result("reused"),
        text("recent"),
        call("reused"),
        result("reused"),
        text("done", "assistant"),
    ]
    current = [text("now"), text("extra current")]

    async def source() -> AsyncGenerator[Message, None]:
        for message in history:
            yield message

    window = ContextWindow(2000)
    actual = await window.select_stream(source(), current)
    assert actual == window.select(history, current)
    assert [item.id for item in actual.messages] == [
        item.id for item in [history[0], *history[7:], *current]
    ]
    history[-1].content[0] = TextContent(text="changed after selection")
    assert actual.messages[-3].content[0] == TextContent(text="done")
