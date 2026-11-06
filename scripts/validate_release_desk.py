"""Install the standalone Release Desk application outside the checkout and verify it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, help="Pre-publication candidate override")
    parser.add_argument(
        "--live", action="store_true", help="Read public GitHub v0.7.0 metadata once"
    )
    parser.add_argument("--report-dir", type=Path, default=Path("build/release-desk-validation"))
    args = parser.parse_args()
    if not os.environ.get("RELEASE_DESK_DATABASE_URL"):
        parser.error("Set RELEASE_DESK_DATABASE_URL to a dedicated disposable PostgreSQL database")
    repository = Path(__file__).resolve().parents[1]
    reports = args.report_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    if (reports / "source-manifest.json").exists():
        parser.error("Choose a new report directory; existing adoption evidence must be preserved")
    source_root = repository / "applications/release_desk"
    sources = [
        Path(__file__).resolve(),
        source_root / "pyproject.toml",
        source_root / "uv.lock",
        *sorted((source_root / "src").rglob("*.py")),
        *sorted((source_root / "tests").rglob("*.py")),
    ]
    hashes = {}
    for source in sources:
        relative = source.relative_to(repository)
        hashes[relative.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
        target = reports / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    (reports / "source-manifest.json").write_text(
        json.dumps(
            {
                "source_hashes": hashes,
                "live_canary": args.live,
                "library_source": "candidate" if args.wheel else "published",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["RELEASE_DESK_NAMESPACE"] = f"release-desk-canary-{uuid4()}"
    environment["RELEASE_DESK_LIBRARY_SOURCE"] = "candidate" if args.wheel else "published"
    environment["RELEASE_DESK_CONTEXT_MAX_BYTES"] = "65536"
    environment["RELEASE_DESK_STREAM_HISTORY"] = "true"
    environment["RELEASE_DESK_MODEL_INPUT_MAX_BYTES"] = "65536"
    environment["RELEASE_DESK_MODEL_TOOLS_MAX_BYTES"] = "8192"
    with tempfile.TemporaryDirectory(prefix="release-desk-") as temporary:
        app = Path(temporary) / "application"
        shutil.copytree(
            repository / "applications/release_desk",
            app,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
        )
        python = app / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        cli = python.with_name("release-desk.exe" if sys.platform == "win32" else "release-desk")

        def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
            return subprocess.run(
                command, cwd=app, env=environment, check=True, text=True, capture_output=capture
            )

        if args.wheel:
            run(["uv", "venv", "--python", sys.executable, str(app / ".venv")])
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    f"{args.wheel.resolve(strict=True)}[postgres]",
                    "pytest==8.4.2",
                    "pytest-asyncio==0.26.0",
                ]
            )
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--no-deps",
                    "--editable",
                    str(app),
                ]
            )
        else:
            run(["uv", "sync", "--locked", "--all-groups"])
        run(["uv", "pip", "check", "--python", str(python)])
        run([str(cli), "migrate"])
        inventory = run(["uv", "pip", "freeze", "--python", str(python)], capture=True)
        (reports / "installed-packages.txt").write_text(inventory.stdout, encoding="utf-8")
        provenance = run(
            [
                str(python),
                "-c",
                "import importlib.metadata,json; "
                "d=importlib.metadata.distribution('agent-runtime-kit'); "
                "print(json.dumps({'version':d.version,'source':json.loads(d.read_text('direct_url.json'))}))",
            ],
            capture=True,
        )
        (reports / "package-audit.json").write_text(provenance.stdout, encoding="utf-8")
        run(
            [
                str(python),
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-W",
                "error::pytest.PytestUnraisableExceptionWarning",
                f"--junitxml={reports / 'results.xml'}",
            ]
        )
        if args.live:
            key = f"public-canary-{uuid4()}"
            command = [str(cli), "review", "Shengtao-Lin/agent-runtime-kit", "v0.7.0", "--key", key]
            streamed = run([*command, "--stream"], capture=True)
            events = [json.loads(line) for line in streamed.stdout.splitlines()]
            assert events[-1]["type"] == "completed"
            response = events[-1]["response"]
            review = response["tool_results"][0]["output"]
            assert review["metadata_ready"]
            assert review["repository"] == command[2]
            assert review["tag"] == command[3]
            environment["RELEASE_DESK_CONTEXT_MAX_BYTES"] = "2"
            environment["RELEASE_DESK_STREAM_HISTORY"] = "false"
            environment["RELEASE_DESK_MODEL_INPUT_MAX_BYTES"] = "2"
            environment["RELEASE_DESK_MODEL_TOOLS_MAX_BYTES"] = "2"
            try:
                replay = json.loads(run(command, capture=True).stdout)
            finally:
                environment["RELEASE_DESK_CONTEXT_MAX_BYTES"] = "65536"
                environment["RELEASE_DESK_STREAM_HISTORY"] = "true"
                environment["RELEASE_DESK_MODEL_INPUT_MAX_BYTES"] = "65536"
                environment["RELEASE_DESK_MODEL_TOOLS_MAX_BYTES"] = "8192"
            assert replay == response
            history = json.loads(
                run([str(cli), "history", response["thread_id"]], capture=True).stdout
            )
            assert len(history) == 4
            feedback_command = [
                str(cli),
                "feedback",
                response["run_id"],
                response["message"]["id"],
                "--key",
                f"feedback-{key}",
                "--value",
                "helpful",
            ]
            feedback = json.loads(run(feedback_command, capture=True).stdout)
            repeated = json.loads(run(feedback_command, capture=True).stdout)
            assert repeated == feedback
            (reports / "public-canary.json").write_text(
                json.dumps(
                    {
                        "repository": review["repository"],
                        "tag": review["tag"],
                        "run_id": response["run_id"],
                        "thread_id": response["thread_id"],
                        "checks": review["checks"],
                        "replay_preserved": True,
                        "feedback_idempotent": True,
                        "context_max_bytes": 65536,
                        "replay_context_max_bytes": 2,
                        "stream_history": True,
                        "replay_stream_history": False,
                        "model_input_max_bytes": 65536,
                        "model_tools_max_bytes": 8192,
                        "replay_model_input_max_bytes": 2,
                        "replay_model_tools_max_bytes": 2,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(
                f"Public canary passed: {review['repository']} {review['tag']}; "
                f"{len(review['checks'])} checks, exact restart replay, history=4, "
                "idempotent feedback"
            )
    print(f"Release Desk reports: {reports}")


if __name__ == "__main__":
    main()
