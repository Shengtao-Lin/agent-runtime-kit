"""Compare matched complete baseline/candidate experiments without inventing performance gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from .summarize_workload import summarize
else:
    from summarize_workload import summarize


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    left, right = baseline["manifest"], candidate["manifest"]
    if left["repetitions"] < 3 or right["repetitions"] != left["repetitions"]:
        raise ValueError("A matched comparison requires at least three repetitions")
    if left["profiles"] != right["profiles"] or left["source_hashes"] != right["source_hashes"]:
        raise ValueError("Profiles and measured workload sources must match")
    if (
        len(left["profiles"]) != 15
        or len(baseline["profiles"]) != 15
        or len(candidate["profiles"]) != 15
    ):
        raise ValueError("Both full experiments require fifteen summarized profiles")
    if left.get("smoke") is not False or right.get("smoke") is not False:
        raise ValueError("Smoke runs cannot establish the full comparison")
    if left.get("context_max_bytes") is not None or right.get("context_max_bytes") != 65536:
        raise ValueError("This comparison requires a default baseline and a 65,536-byte candidate")
    dependencies = [
        {
            name: version
            for name, version in report["installed"]["packages"].items()
            if name != "agent-runtime-kit"
        }
        for report in (baseline, candidate)
    ]
    if dependencies[0] != dependencies[1]:
        raise ValueError("Installed dependency inventories must match")
    groups = []
    for before, after in zip(baseline["profiles"], candidate["profiles"], strict=True):
        if any(
            before[key] != after[key]
            for key in (
                "profile",
                "environment",
                "samples",
                "max_history_rows",
                "history_json_bytes",
            )
        ):
            raise ValueError(
                "Host environment, sample counts and effective historical sizes must match"
            )
        if after["initial_context_bytes"]["max"] > 65536:
            raise ValueError("Candidate violates the initial-context requirement")
        groups.append(
            {
                "profile": before["profile"],
                "samples": before["samples"],
                "initial_context_bytes": {
                    "baseline": before["initial_context_bytes"],
                    "candidate": after["initial_context_bytes"],
                },
                "max_model_input_bytes": {
                    "baseline": before["max_model_input_bytes"],
                    "candidate": after["max_model_input_bytes"],
                },
                "rss_measurement_peak_bytes": {
                    "baseline": before["rss_measurement_peak_bytes"],
                    "candidate": after["rss_measurement_peak_bytes"],
                },
                "timings": {"baseline": before["timings"], "candidate": after["timings"]},
            }
        )
    return {
        "status": "matched",
        "baseline_manifest": left,
        "candidate_manifest": right,
        "profiles": groups,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(compare(summarize(arguments.baseline), summarize(arguments.candidate)), indent=2)
        + "\n",
        encoding="utf-8",
    )
