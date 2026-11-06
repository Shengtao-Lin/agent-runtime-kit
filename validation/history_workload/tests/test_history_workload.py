"""Reject contaminated measurement lanes and verify real installed-wheel instrumentation."""

import argparse
import json

import pytest
from history_workload import MIB, host_budgets, main, safe_plan


def test_host_budget_misses_are_recorded_without_moving_the_threshold():
    report = {
        "profile": {"concurrency": 1, "mode": "invoke", "lane": "timing"},
        "samples": [{"end_to_end_seconds": 2.01, "context": {"selected_bytes": 65536}}],
        "rss_before_measurement": 100 * MIB,
        "rss_measurement_peak_bytes": 229 * MIB,
        "python_peak_bytes": None,
    }
    assert host_budgets(report) == {
        "responsiveness": False,
        "rss_increase": False,
        "python_peak": True,
        "initial_context": True,
    }
    report["profile"]["lane"] = "diagnostic"
    report["python_peak_bytes"] = 129 * MIB
    assert not host_budgets(report)["python_peak"]
    assert host_budgets(report)["responsiveness"]


def test_query_plan_evidence_does_not_include_sql_literals_or_predicates():
    plan = [
        {
            "Plan": {
                "Node Type": "Index Scan",
                "Index Cond": "secret-id",
                "Actual Rows": 4,
                "Output": ["message-body"],
            },
            "Query Text": "private-query",
            "Execution Time": 1,
        }
    ]
    safe = safe_plan(plan)
    assert safe == [{"Plan": {"Node Type": "Index Scan", "Actual Rows": 4}, "Execution Time": 1}]


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["timing", "diagnostic"])
async def test_installed_history_probes_and_separate_timing_lane(tmp_path, lane):
    path = tmp_path / f"{lane}.json"
    await main(
        argparse.Namespace(
            history_turns=10,
            concurrency=1,
            mode="stream",
            lane=lane,
            smoke=True,
            report=path,
        )
    )
    report = json.loads(path.read_text())
    assert report["status"] == "passed"
    assert report["rss_sampler_stopped"]
    assert report["remaining_database_sessions"] == report["checked_out_connections"] == 0
    assert report["final_history"][0]["rows"] == 48
    assert all(report["host_budgets"].values())
    for index, sample in enumerate(report["samples"]):
        assert sample["context"]["history_messages"] == 40 + 4 * index
        assert 0 < sample["first_text_seconds"] <= sample["end_to_end_seconds"]
        if lane == "timing":
            assert "history_read_seconds" not in sample
            assert "driver_json_values" not in sample
            assert report["python_peak_bytes"] is None
            assert "separate_server_plan" not in report
        else:
            assert sample["history_rows"] == sample["canonical_conversion_rows"] == 40 + 4 * index
            assert 0 < sample["canonical_conversion_seconds"] < sample["history_read_seconds"]
            assert sample["driver_json_decode_seconds"] > 0
            assert sample["driver_json_bytes"] > 0
            assert sample["context_selection_seconds"] > 0
            assert sample["history_cursor_seconds"] > 0
            assert report["python_peak_bytes"] > 0
            assert report["separate_server_plan"][0]["Plan"]["Actual Rows"] == 48
