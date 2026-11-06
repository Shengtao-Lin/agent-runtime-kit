"""Run the frozen Stage E history workload in separate timing and diagnostic processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

BASELINE_VERSION = "0.6.0"
BASELINE_SHA256 = "abe01feae67feab23f5a5625a3a19f99914bd24e10d87da711f14a119e027798"
BASELINE_URL = (
    "https://github.com/Shengtao-Lin/agent-runtime-kit/releases/download/v0.6.0/"
    "agent_runtime_kit-0.6.0-py3-none-any.whl"
)


def profiles(smoke: bool) -> list[tuple[int, int, str, str]]:
    cases = (
        [(100, 1, "invoke"), (10, 4, "stream")]
        if smoke
        else [
            *[(turns, 1, mode) for turns in (100, 1000, 10000) for mode in ("invoke", "stream")],
            (1000, 4, "stream"),
        ]
    )
    return [
        (turns, concurrency, mode, lane)
        for turns, concurrency, mode in cases
        for lane in ("timing", "diagnostic")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel", type=Path, help="Optional candidate; baseline stays checksum-pinned"
    )
    parser.add_argument("--expected-version", default=BASELINE_VERSION)
    parser.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("WORKLOAD_DATABASE_URL"):
        parser.error("Set WORKLOAD_DATABASE_URL to a dedicated disposable PostgreSQL database")
    repository = Path(__file__).resolve().parents[1]
    source = repository / "validation/history_workload"
    reports = args.report_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    if (reports / "manifest.json").exists():
        parser.error(
            "Report directory already has a manifest; choose a new directory to preserve evidence"
        )
    lock = tomllib.loads((source / "uv.lock").read_text(encoding="utf-8"))
    baseline = next(item for item in lock["package"] if item["name"] == "agent-runtime-kit")
    assert baseline["version"] == BASELINE_VERSION
    assert baseline["wheels"] == [{"url": BASELINE_URL, "hash": f"sha256:{BASELINE_SHA256}"}]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["DATABASE_URL"] = environment["WORKLOAD_DATABASE_URL"]
    manifest = {
        "target": "candidate" if args.wheel else "published-baseline",
        "expected_version": args.expected_version,
        "wheel_sha256": hashlib.sha256(args.wheel.read_bytes()).hexdigest()
        if args.wheel
        else BASELINE_SHA256,
        "baseline_url": BASELINE_URL,
        "repetitions": args.repetitions,
        "smoke": args.smoke,
        "context_max_bytes": 65536,
        "specification_commit": "d587d1f",
        "profiles": profiles(args.smoke),
        "source_hashes": {
            path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                Path(__file__).resolve(),
                repository / "docs/validation/stage-e-workload.md",
                repository / "validation/workloads/workload.py",
                *sorted(source.rglob("*.py")),
                source / "pyproject.toml",
                source / "uv.lock",
                *sorted((repository / "applications/release_desk/src").rglob("*.py")),
            ]
        },
        "status": "running",
    }
    manifest_path = reports / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="runtime-history-") as temporary:
        root = Path(temporary)
        consumer = root / "consumer"
        shutil.copytree(
            source, consumer, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache")
        )
        shutil.copy2(
            repository / "validation/workloads/workload.py", consumer / "stage_d_fixtures.py"
        )
        shutil.copytree(
            repository / "applications/release_desk/src/release_desk",
            consumer / "release_desk",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        python = consumer / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

        def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                command,
                cwd=consumer,
                env=environment,
                check=True,
                text=True,
                capture_output=capture,
                timeout=300,
            )

        try:
            run(["uv", "sync", "--locked", "--python", sys.executable])
            if args.wheel:
                run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(python),
                        "--no-deps",
                        "--reinstall-package",
                        "agent-runtime-kit",
                        str(args.wheel.resolve(strict=True)),
                    ]
                )
            run(["uv", "pip", "check", "--python", str(python)])
            audit = run(
                [
                    str(python),
                    "-c",
                    "import agent_runtime,importlib.metadata as m,json,pathlib,sys; "
                    "d=m.distribution('agent-runtime-kit'); "
                    "location=pathlib.Path(agent_runtime.__file__); "
                    "assert location.is_relative_to(pathlib.Path(sys.prefix)); "
                    "source=json.loads(d.read_text('direct_url.json')); "
                    "assert not source.get('dir_info',{}).get('editable'); "
                    f"assert d.version == {args.expected_version!r}; "
                    "print(json.dumps({'version':d.version,'source':source,'packages':"
                    "{p.metadata['Name']:p.version for p in m.distributions()}},indent=2))",
                ],
                capture=True,
            )
            (reports / "package-audit.json").write_text(audit.stdout, encoding="utf-8")
            run([str(python), "-m", "agent_runtime.migrations", "upgrade"])
            run(
                [
                    str(python),
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=short",
                    "-p",
                    "no:cacheprovider",
                    f"--basetemp={root / 'pytest-temp'}",
                    f"--junitxml={reports / 'tests.xml'}",
                ]
            )
            for repetition in range(1, args.repetitions + 1):
                for turns, concurrency, mode, lane in profiles(args.smoke):
                    filename = f"r{repetition}-h{turns}-c{concurrency}-{mode}-{lane}.json"
                    command = [
                        str(python),
                        "history_workload.py",
                        "--history-turns",
                        str(turns),
                        "--concurrency",
                        str(concurrency),
                        "--lane",
                        lane,
                        "--mode",
                        mode,
                        "--report",
                        str(reports / filename),
                    ]
                    if args.smoke:
                        command.append("--smoke")
                    run(command)
            manifest["status"] = "passed"
        except BaseException as exc:
            manifest["status"] = "failed"
            manifest["failure_type"] = type(exc).__name__
            raise
        finally:
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Workload evidence: {reports}")


if __name__ == "__main__":
    main()
