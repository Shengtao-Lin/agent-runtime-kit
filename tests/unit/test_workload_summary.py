from pathlib import Path

import pytest

from scripts.summarize_workload import summarize


def test_workload_summary_rejects_failed_experiment(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text('{"status": "failed"}', encoding="utf-8")
    with pytest.raises(ValueError, match="completed, passing"):
        summarize(tmp_path)


def test_workload_summary_rejects_missing_repetition(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        '{"status": "passed", "repetitions": 3, "profiles": [[10, 1, 1, "invoke"]]}',
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="r1-h10-c1-p1-invoke"):
        summarize(tmp_path)
