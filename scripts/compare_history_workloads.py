"""Summarize completed Stage E runs and enforce the frozen matched-candidate requirements."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

if __package__:
    from .validate_history_workload import BASELINE_SHA256, BASELINE_VERSION, profiles
else:
    from validate_history_workload import BASELINE_SHA256, BASELINE_VERSION, profiles

MIB = 1024 * 1024


def stats(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered or any(not math.isfinite(value) or value < 0 for value in ordered):
        raise ValueError("Expected finite nonnegative metric samples")
    return {
        "p50": ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)],
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        "max": ordered[-1],
    }


def check_report(
    report: dict[str, Any], profile: tuple[int, int, str, str], version: str
) -> dict[str, bool]:
    turns, concurrency, mode, lane = profile
    expected = {
        "history_turns": turns,
        "concurrency": concurrency,
        "mode": mode,
        "lane": lane,
        "smoke": report["profile"]["smoke"],
    }
    if (
        report["status"] != "passed"
        or report["profile"] != expected
        or report["version"] != version
    ):
        raise ValueError("Incomplete or mismatched raw experiment")
    if not report["rss_sampler_stopped"] or not report["exact_replay_without_work"]:
        raise ValueError("Missing sampler or exact replay acceptance")
    if any(
        report[key] != 0
        for key in (
            "checked_out_connections",
            "remaining_database_sessions",
            "surviving_async_tasks",
        )
    ):
        raise ValueError("Experiment leaked resources")
    rounds = 2 if expected["smoke"] else (3 if lane == "diagnostic" else 20 // concurrency)
    samples = report["samples"]
    if len(samples) != rounds * concurrency:
        raise ValueError("Missing measured continuations")
    observed_turns = sorted(item["turn"] for item in samples)
    if observed_turns != sorted(list(range(rounds)) * concurrency):
        raise ValueError("Incorrect per-thread continuation counts")
    for key, count in (("seeded_history", turns * 4), ("final_history", (turns + rounds) * 4)):
        if len(report[key]) != concurrency or any(item["rows"] != count for item in report[key]):
            raise ValueError("Incorrect durable history count")
    for sample in samples:
        context = sample["context"]
        if context["history_messages"] != (turns + sample["turn"]) * 4:
            raise ValueError("Incorrect effective history size")
        if context["max_bytes"] != 65536 or context["selected_bytes"] > 65536:
            raise ValueError("Context allowance violated")
        if (
            mode == "stream"
            and not 0 < sample["first_text_seconds"] <= sample["end_to_end_seconds"]
        ):
            raise ValueError("Missing or invalid first-text latency")
        if lane == "timing":
            if report["python_peak_bytes"] is not None or "history_read_seconds" in sample:
                raise ValueError("Timing lane contains diagnostic probes")
        else:
            if sample["canonical_conversion_rows"] != context["history_messages"]:
                raise ValueError("Missing installed-store conversion observations")
            if sample["driver_json_values"] < context["history_messages"]:
                raise ValueError("Missing driver JSON observations")
            if sample["history_queries"] < 1 or len(sample["model_input_bytes"]) != 2:
                raise ValueError("Missing query/model observations")
    latency = "first_text_seconds" if mode == "stream" else "end_to_end_seconds"
    measured_latency = stats([sample[latency] for sample in samples])["p95"]
    increase = max(0, report["rss_measurement_peak_bytes"] - report["rss_before_measurement"])
    budgets = {
        "responsiveness": concurrency != 1 or lane != "timing" or measured_latency <= 2,
        "rss_increase": concurrency != 1 or increase <= 128 * MIB,
        "python_peak": concurrency != 1
        or lane != "diagnostic"
        or report["python_peak_bytes"] <= 128 * MIB,
        "initial_context": True,
    }
    if budgets != report["host_budgets"]:
        raise ValueError("Recorded budget results disagree with raw measurements")
    return budgets


def summarize(directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text())
    installed = json.loads((directory / "package-audit.json").read_text())
    if manifest["status"] != "passed" or installed["version"] != manifest["expected_version"]:
        raise ValueError("Incomplete manifest or installed version mismatch")
    expected_profiles = [list(item) for item in profiles(manifest["smoke"])]
    if manifest["profiles"] != expected_profiles or manifest["context_max_bytes"] != 65536:
        raise ValueError("Manifest does not match the frozen matrix")
    groups = []
    for turns, concurrency, mode, lane in expected_profiles:
        raw = []
        records = []
        for repetition in range(1, manifest["repetitions"] + 1):
            name = f"r{repetition}-h{turns}-c{concurrency}-{mode}-{lane}.json"
            data = (directory / name).read_bytes()
            report = json.loads(data)
            if report["profile"]["smoke"] != manifest["smoke"]:
                raise ValueError("Smoke status mismatch")
            budgets = check_report(report, (turns, concurrency, mode, lane), installed["version"])
            records.append(
                {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "host_budgets": budgets}
            )
            raw.append(report)
        environments = [
            {key: report[key] for key in ("python", "platform", "postgres_version")}
            for report in raw
        ]
        if any(item != environments[0] for item in environments):
            raise ValueError("Environment changed within an experiment")
        samples = [sample for report in raw for sample in report["samples"]]
        timing_keys = [
            key for key in samples[0] if key.endswith("_seconds") and samples[0][key] is not None
        ]
        groups.append(
            {
                "profile": raw[0]["profile"],
                "environment": environments[0],
                "samples": len(samples),
                "raw": records,
                "history_shapes": [
                    {key: report[key] for key in ("seeded_history", "final_history")}
                    for report in raw
                ],
                "timings": {key: stats([sample[key] for sample in samples]) for key in timing_keys},
                "rss_absolute_peak_bytes": max(
                    report["rss_measurement_peak_bytes"] for report in raw
                ),
                "rss_increase_bytes": max(
                    max(0, report["rss_measurement_peak_bytes"] - report["rss_before_measurement"])
                    for report in raw
                ),
                "python_peak_bytes": max(report["python_peak_bytes"] for report in raw)
                if lane == "diagnostic"
                else None,
                "initial_context_bytes": stats(
                    [sample["context"]["selected_bytes"] for sample in samples]
                ),
                "all_host_budgets_met": all(all(item["host_budgets"].values()) for item in records),
            }
        )
    return {"manifest": manifest, "installed": installed, "profiles": groups}


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    left, right = baseline["manifest"], candidate["manifest"]
    if (
        left["smoke"]
        or right["smoke"]
        or left["repetitions"] < 3
        or right["repetitions"] != left["repetitions"]
    ):
        raise ValueError("A full comparison requires three or more matched repetitions")
    if left["expected_version"] != BASELINE_VERSION or left["wheel_sha256"] != BASELINE_SHA256:
        raise ValueError("Baseline is not the frozen actual v0.6.0 wheel")
    if left["profiles"] != right["profiles"] or left["source_hashes"] != right["source_hashes"]:
        raise ValueError("Measured sources and profiles must match")
    dependencies = [
        {k: v for k, v in report["installed"]["packages"].items() if k != "agent-runtime-kit"}
        for report in (baseline, candidate)
    ]
    if dependencies[0] != dependencies[1]:
        raise ValueError("Installed dependencies must match")
    if len(baseline["profiles"]) != 14 or len(candidate["profiles"]) != 14:
        raise ValueError("A full comparison requires fourteen profile/lane combinations")
    groups = []
    for before, after in zip(baseline["profiles"], candidate["profiles"], strict=True):
        if any(
            before[key] != after[key]
            for key in ("profile", "environment", "samples", "history_shapes")
        ):
            raise ValueError("Environment, effective data and sample counts must match")
        regression = True
        regression_threshold = None
        if before["profile"]["history_turns"] == 100 and before["profile"]["lane"] == "timing":
            metric = (
                "first_text_seconds"
                if before["profile"]["mode"] == "stream"
                else "end_to_end_seconds"
            )
            latency = before["timings"][metric]["p95"]
            regression_threshold = latency + max(0.02, 0.2 * latency)
            regression = after["timings"][metric]["p95"] <= regression_threshold
        groups.append(
            {
                "profile": before["profile"],
                "baseline": before,
                "candidate": after,
                "short_history_regression_passed": regression,
                "short_history_threshold_seconds": regression_threshold,
            }
        )
    accepted = all(
        item["candidate"]["all_host_budgets_met"] and item["short_history_regression_passed"]
        for item in groups
    )
    return {
        "status": "accepted" if accepted else "budget_miss",
        "baseline_manifest": left,
        "candidate_manifest": right,
        "profiles": groups,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path, nargs="?")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.baseline)
    if args.candidate:
        result = compare(result, summarize(args.candidate))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if result.get("status") == "budget_miss":
        raise SystemExit("Candidate violates a frozen Stage E host requirement")
