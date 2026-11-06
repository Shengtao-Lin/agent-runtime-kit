"""Validate measurement units, seeded relationships and real installed-wheel continuations."""

import argparse
import json

import pytest
from workload import clone_turn, main, message_bytes

from agent_runtime import Message, TextContent, ToolCall, ToolCallContent, ToolResultContent


def test_byte_metric_is_utf8_canonical_json_not_character_count():
    messages = [Message(role="user", content=[TextContent(text="é")])]
    serialized = json.dumps(
        [item.model_dump(mode="json") for item in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert message_bytes(messages) == len(serialized.encode("utf-8"))
    assert message_bytes(messages) > len(serialized)


def test_seed_turn_keeps_links_without_reusing_message_or_call_ids():
    turn = [
        Message(role="user", content=[TextContent(text="review")]),
        Message(
            role="assistant",
            content=[
                ToolCallContent(tool_call=ToolCall(id="original", name="review", arguments={}))
            ],
        ),
        Message(
            role="tool", content=[ToolResultContent(tool_call_id="original", result={"ok": True})]
        ),
        Message(role="assistant", content=[TextContent(text="done")]),
    ]
    copy = clone_turn(turn)
    assert {item.id for item in copy}.isdisjoint(item.id for item in turn)
    assert copy[1].content[0].tool_call.id == copy[2].content[0].tool_call_id != "original"
    assert turn[1].content[0].tool_call.id == "original"
    assert [item.role for item in copy] == [item.role for item in turn]


@pytest.mark.asyncio
@pytest.mark.parametrize("turns", [1, 10])
async def test_real_continuations_and_seeded_history_share_shape(tmp_path, turns):
    report = tmp_path / "report.json"
    await main(
        argparse.Namespace(
            history_turns=turns,
            concurrency=1,
            padding_kib=1,
            mode="stream",
            smoke=True,
            report=report,
        )
    )
    result = json.loads(report.read_text())
    assert result["status"] == "passed"
    assert result["rss_sampler_stopped"]
    assert result["rss_measurement_peak_bytes"] > 0
    assert [sample["history"][0]["messages"] for sample in result["samples"]] == [
        4 * turns,
        4 * (turns + 1),
    ]
    for sample in result["samples"]:
        assert sample["end_to_end_seconds"] >= sample["service_seconds"]
        assert sample["first_text_from_submission_seconds"] > 0
        assert sample["model_inputs"][1]["json_bytes"] > sample["invoker_inputs"][0]["json_bytes"]
