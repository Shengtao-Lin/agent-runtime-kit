"""Do not turn insufficient or changed experiments into paired evidence."""

from typing import Any

import pytest

from scripts.compare_workloads import compare


def test_comparison_rejects_insufficient_repetition() -> None:
    report: dict[str, Any] = {"manifest": {"repetitions": 1}}
    with pytest.raises(ValueError, match="three repetitions"):
        compare(report, report)


def test_comparison_rejects_changed_measurement_source() -> None:
    baseline: dict[str, Any] = {
        "manifest": {"repetitions": 3, "profiles": [], "source_hashes": {"workload.py": "before"}}
    }
    candidate: dict[str, Any] = {
        "manifest": {"repetitions": 3, "profiles": [], "source_hashes": {"workload.py": "after"}}
    }
    with pytest.raises(ValueError, match="sources must match"):
        compare(baseline, candidate)
