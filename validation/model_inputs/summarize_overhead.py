"""Apply the premeasurement gates to independent matched-process observations."""

import json
import math
import os
from pathlib import Path


def main():
    root = Path(os.environ["MODEL_INPUT_REPORTS"])

    def read(kind, repeat, candidate, mode, size):
        return json.loads(
            (root / f"overhead-{kind}-{repeat}-{candidate}-{mode}-{size}.json").read_text()
        )

    def p95(values):
        return sorted(values)[math.ceil(len(values) * 0.95) - 1]

    summary = []
    for mode in ("generate", "stream"):
        for size in (1024, 16384, 65536):
            peaks = [
                read("allocation", 0, candidate, mode, size)["python_peak_bytes"]
                for candidate in (False, True)
            ]
            assert peaks[1] - peaks[0] <= 4 * 1024 * 1024
            for repeat in range(3):
                pair = [
                    read("timing", repeat, candidate, mode, size)["nanoseconds"]
                    for candidate in (False, True)
                ]
                assert len(pair[0]) == len(pair[1]) == 500
                added = p95([after - before for before, after in zip(*pair, strict=True)])
                assert added <= 10_000_000
                summary.append(
                    {
                        "mode": mode,
                        "message_bytes": size,
                        "repeat": repeat,
                        "baseline_p95_ms": p95(pair[0]) / 1e6,
                        "candidate_p95_ms": p95(pair[1]) / 1e6,
                        "added_p95_ms": added / 1e6,
                        "added_python_peak_bytes": peaks[1] - peaks[0],
                    }
                )
    (root / "overhead-summary.json").write_text(
        json.dumps({"status": "passed", "profiles": summary}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
