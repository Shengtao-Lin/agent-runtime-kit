"""Summarize a complete workload run without treating partial profiles as successful evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any


def summarize(directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "passed":
        raise ValueError("Only a completed, passing workload run may be summarized")
    groups = []
    report_hashes = {}
    for history, concurrency, padding, mode in manifest["profiles"]:
        repeats = []
        for repetition in range(1, manifest["repetitions"] + 1):
            name = f"r{repetition}-h{history}-c{concurrency}-p{padding}-{mode}.json"
            path = directory / name
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["status"] == "passed"
            assert data["version"] == manifest["expected_version"]
            assert data.get("context_max_bytes") == manifest.get("context_max_bytes")
            assert data["profile"]["smoke"] == manifest.get("smoke", data["profile"]["smoke"])
            assert data["profile"] == {
                "history_turns": history,
                "concurrency": concurrency,
                "padding_kib": padding,
                "mode": mode,
                "smoke": data["profile"]["smoke"],
            }
            assert data["exact_replay_without_work"] and data["rss_sampler_stopped"]
            rounds = 2 if data["profile"]["smoke"] else max(2, math.ceil(20 / concurrency))
            assert len(data["samples"]) == rounds * concurrency
            for sample in data["samples"]:
                assert len(sample["history"]) == len(sample["invoker_inputs"]) == 1
                assert len(sample["model_inputs"]) == 2
                assert sample["history"][0]["messages"] == 4 * (history + sample["turn"])
            assert (
                data["remaining_database_sessions"]
                == data["checked_out_connections"]
                == data["surviving_async_tasks"]
                == 0
            )
            repeats.append(data)
            report_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        samples = [sample for repeat in repeats for sample in repeat["samples"]]

        def timing(field, samples=samples):
            values = sorted(sample[field] * 1000 for sample in samples if sample[field] is not None)
            return (
                {
                    "samples": len(values),
                    "p50_ms": statistics.median(values),
                    "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
                }
                if values
                else None
            )

        initial_sizes = [sample["invoker_inputs"][0]["json_bytes"] for sample in samples]
        groups.append(
            {
                "profile": repeats[0]["profile"],
                "environment": {
                    field: repeats[0][field]
                    for field in ("python", "platform", "clock_resolution_seconds")
                },
                "repetitions": len(repeats),
                "samples": len(samples),
                "initial_context_bytes": {"min": min(initial_sizes), "max": max(initial_sizes)},
                "samples_over_64kib_initial_context": sum(size > 65536 for size in initial_sizes),
                "max_model_input_bytes": max(
                    item["json_bytes"] for sample in samples for item in sample["model_inputs"]
                ),
                "max_history_rows": max(
                    item["messages"] for sample in samples for item in sample["history"]
                ),
                "history_json_bytes": {
                    "min": min(
                        item["json_bytes"] for sample in samples for item in sample["history"]
                    ),
                    "max": max(
                        item["json_bytes"] for sample in samples for item in sample["history"]
                    ),
                },
                "rss_measurement_peak_bytes": {
                    "min": min(item["rss_measurement_peak_bytes"] for item in repeats),
                    "max": max(item["rss_measurement_peak_bytes"] for item in repeats),
                },
                "python_peak_bytes_max": max(item["python_peak_bytes"] for item in repeats),
                "timings": {
                    name: timing(name)
                    for name in (
                        "end_to_end_seconds",
                        "queue_seconds",
                        "service_seconds",
                        "history_read_seconds",
                        "measurement_seconds",
                        "first_text_from_submission_seconds",
                        "first_text_from_admission_seconds",
                    )
                },
            }
        )
    return {
        "manifest": manifest,
        "installed": json.loads((directory / "package-audit.json").read_text(encoding="utf-8")),
        "profiles": groups,
        "raw_report_sha256": report_hashes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.write_text(
        json.dumps(summarize(arguments.report_dir), indent=2) + "\n", encoding="utf-8"
    )
