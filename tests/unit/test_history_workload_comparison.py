"""Reject incomplete, contaminated or misleading Stage E acceptance evidence."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts.compare_history_workloads import check_report, compare, summarize
from scripts.validate_history_workload import BASELINE_SHA256, profiles


def raw_timing() -> dict[str, Any]:
    return {
        "version": "0.6.0",
        "status": "passed",
        "profile": {
            "history_turns": 100,
            "concurrency": 1,
            "mode": "invoke",
            "lane": "timing",
            "smoke": True,
        },
        "rss_sampler_stopped": True,
        "exact_replay_without_work": True,
        "checked_out_connections": 0,
        "remaining_database_sessions": 0,
        "surviving_async_tasks": 0,
        "rss_before_measurement": 100,
        "rss_measurement_peak_bytes": 200,
        "python_peak_bytes": None,
        "seeded_history": [{"rows": 400}],
        "final_history": [{"rows": 408}],
        "samples": [
            {
                "turn": turn,
                "context": {
                    "history_messages": 400 + 4 * turn,
                    "max_bytes": 65536,
                    "selected_bytes": 123,
                },
                "end_to_end_seconds": 0.1,
            }
            for turn in range(2)
        ],
        "host_budgets": {
            "responsiveness": True,
            "rss_increase": True,
            "python_peak": True,
            "initial_context": True,
        },
    }


def test_raw_budget_verifier_rejects_forged_success():
    report = raw_timing()
    assert all(check_report(report, (100, 1, "invoke", "timing"), "0.6.0").values())
    report["samples"][0]["end_to_end_seconds"] = 3
    with pytest.raises(ValueError, match="disagree"):
        check_report(report, (100, 1, "invoke", "timing"), "0.6.0")
    report["host_budgets"]["responsiveness"] = False
    assert not check_report(report, (100, 1, "invoke", "timing"), "0.6.0")["responsiveness"]


@pytest.mark.parametrize(
    "defect", ["incomplete", "leak", "diagnostic", "missing", "history", "context"]
)
def test_raw_verifier_rejects_incomplete_or_contaminated_evidence(defect: str) -> None:
    report = raw_timing()
    if defect == "incomplete":
        report["status"] = "failed"
    elif defect == "leak":
        report["remaining_database_sessions"] = 1
    elif defect == "diagnostic":
        report["python_peak_bytes"] = 10
    elif defect == "missing":
        report["samples"].pop()
    elif defect == "history":
        report["final_history"][0]["rows"] = 404
    else:
        report["samples"][0]["context"]["selected_bytes"] = 65537
    with pytest.raises(ValueError):
        check_report(report, (100, 1, "invoke", "timing"), "0.6.0")


def summary() -> dict[str, Any]:
    return {
        "manifest": {
            "smoke": False,
            "repetitions": 3,
            "expected_version": "0.6.0",
            "wheel_sha256": BASELINE_SHA256,
            "profiles": profiles(False),
            "source_hashes": {"workload": "fixed"},
        },
        "installed": {"packages": {"agent-runtime-kit": "0.6.0", "psycopg": "locked"}},
        "profiles": [
            {
                "profile": {
                    "history_turns": turns,
                    "concurrency": concurrency,
                    "mode": mode,
                    "lane": lane,
                    "smoke": False,
                },
                "environment": {"platform": "fixed"},
                "samples": 60,
                "history_shapes": ["fixed"],
                "all_host_budgets_met": True,
                "timings": {"end_to_end_seconds": {"p95": 0.1}, "first_text_seconds": {"p95": 0.1}},
            }
            for turns, concurrency, mode, lane in profiles(False)
        ],
    }


def test_comparison_keeps_short_history_regression_gate():
    baseline = summary()
    candidate = deepcopy(baseline)
    assert compare(baseline, candidate)["status"] == "accepted"
    candidate["profiles"][0]["timings"]["end_to_end_seconds"]["p95"] = 0.13
    assert compare(baseline, candidate)["status"] == "budget_miss"


def test_baseline_cannot_claim_incremental_history() -> None:
    baseline = summary()
    candidate = deepcopy(baseline)
    baseline["manifest"]["stream_history"] = True
    with pytest.raises(ValueError, match="does not support"):
        compare(baseline, candidate)


@pytest.mark.parametrize("mode", [False, "true", 1])
def test_summary_rejects_mismatched_read_mode(tmp_path: Path, mode: object) -> None:
    manifest = summary()["manifest"]
    manifest.update(
        status="passed",
        smoke=True,
        profiles=profiles(True),
        context_max_bytes=65536,
        stream_history=True,
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "package-audit.json").write_text(json.dumps({"version": "0.6.0"}))
    report = raw_timing()
    report["stream_history"] = mode
    (tmp_path / "r1-h100-c1-invoke-timing.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="read mode"):
        summarize(tmp_path)


@pytest.mark.parametrize(
    "defect",
    ["smoke", "repetitions", "sources", "environment", "dependencies", "history", "baseline"],
)
def test_comparison_rejects_unmatched_or_nonbaseline_inputs(defect: str) -> None:
    baseline = summary()
    candidate = deepcopy(baseline)
    if defect in ("smoke", "repetitions"):
        candidate["manifest"][defect] = True if defect == "smoke" else 1
    elif defect == "sources":
        candidate["manifest"]["source_hashes"] = {"workload": "changed"}
    elif defect == "environment":
        candidate["profiles"][0]["environment"]["platform"] = "different"
    elif defect == "dependencies":
        candidate["installed"]["packages"]["psycopg"] = "different"
    elif defect == "history":
        candidate["profiles"][0]["history_shapes"] = ["smaller"]
    else:
        baseline["manifest"]["wheel_sha256"] = "not-actual-v060"
    with pytest.raises(ValueError):
        compare(baseline, candidate)
