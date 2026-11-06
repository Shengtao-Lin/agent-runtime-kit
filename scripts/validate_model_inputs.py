"""Observe the frozen published model-input baseline in an external installed-wheel venv."""

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
import urllib.request
from pathlib import Path

BASELINE_VERSION = "0.7.0"
BASELINE_SHA256 = "17464f89c4516b9fc3ef706db54c7b69ea5de4d6dfc67049f650d449464b39cd"
BASELINE_URL = (
    "https://github.com/Shengtao-Lin/agent-runtime-kit/releases/download/v0.7.0/"
    "agent_runtime_kit-0.7.0-py3-none-any.whl"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("MODEL_INPUT_DATABASE_URL"):
        parser.error("Set MODEL_INPUT_DATABASE_URL to an owned disposable PostgreSQL database")
    repository = Path(__file__).resolve().parents[1]
    source = repository / "validation/model_inputs"
    app_source = source / "release_desk_source"
    host_identity = json.loads((source / "host-source.json").read_text(encoding="utf-8"))
    assert host_identity["commit"] == "4b896d7708431e90013f482ec2a82777c671f730"
    for name, digest in host_identity["files"].items():
        assert hashlib.sha256((app_source / name).read_bytes()).hexdigest() == digest
    reports = args.report_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    if (reports / "manifest.json").exists():
        parser.error("Choose a new report directory; existing evidence must be preserved")
    lock = tomllib.loads((source / "uv.lock").read_text(encoding="utf-8"))
    package = next(p for p in lock["package"] if p["name"] == "agent-runtime-kit")
    assert package["version"] == BASELINE_VERSION
    assert package["wheels"] == [{"url": BASELINE_URL, "hash": f"sha256:{BASELINE_SHA256}"}]
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["DATABASE_URL"] = environment["MODEL_INPUT_DATABASE_URL"]
    environment["MODEL_INPUT_REPORTS"] = str(reports)
    paths = [
        Path(__file__).resolve(),
        repository / "docs/validation/stage-f-workload.md",
        source / "pyproject.toml",
        source / "uv.lock",
        source / "host-source.json",
        *sorted(source.rglob("*.py")),
    ]
    manifest = {
        "status": "running",
        "baseline_version": BASELINE_VERSION,
        "baseline_wheel_sha256": BASELINE_SHA256,
        "host_source": host_identity,
        "specification_commit": "51c3719",
        "message_budget_bytes": 65536,
        "tool_budget_bytes": 8192,
        "turns_per_mode": 60,
        "modes": ["invoke", "stream"],
        "source_hashes": {
            path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
    }
    manifest_path = reports / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    # Preserve exact measured source bytes separately from normalized git line endings.
    for path in paths:
        destination = reports / "sources" / path.relative_to(repository)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    try:
        with urllib.request.urlopen(BASELINE_URL, timeout=30) as response:
            wheel_bytes = response.read(5 * 1024 * 1024 + 1)
        assert hashlib.sha256(wheel_bytes).hexdigest() == BASELINE_SHA256
        wheel_path = reports / "agent_runtime_kit-0.7.0-py3-none-any.whl"
        wheel_path.write_bytes(wheel_bytes)
        with tempfile.TemporaryDirectory(prefix="runtime-model-inputs-") as temporary:
            root = Path(temporary)
            consumer = root / "consumer"
            shutil.copytree(
                source,
                consumer,
                ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
            )
            shutil.copytree(
                app_source, consumer / "release_desk", ignore=shutil.ignore_patterns("__pycache__")
            )
            python = (
                consumer / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            )

            def run(command: list[str], *, capture: bool = False):
                try:
                    return subprocess.run(
                        command,
                        cwd=consumer,
                        env=environment,
                        check=True,
                        text=True,
                        capture_output=capture,
                        timeout=180,
                    )
                except subprocess.CalledProcessError as exc:
                    if capture:
                        (reports / "failure.stdout.txt").write_text(
                            exc.stdout or "", encoding="utf-8"
                        )
                        (reports / "failure.stderr.txt").write_text(
                            exc.stderr or "", encoding="utf-8"
                        )
                    raise

            run(["uv", "sync", "--locked", "--python", sys.executable])
            run(["uv", "pip", "check", "--python", str(python)])
            audit = run(
                [
                    str(python),
                    "-c",
                    "import agent_runtime,importlib.metadata as m,json,pathlib,sys,platform; "
                    "import zipfile; "
                    "d=m.distribution('agent-runtime-kit'); "
                    "location=pathlib.Path(agent_runtime.__file__); "
                    "assert location.is_relative_to(pathlib.Path(sys.prefix)); "
                    "source=json.loads(d.read_text('direct_url.json')); "
                    f"assert d.version=={BASELINE_VERSION!r}; "
                    f"assert source['url']=={BASELINE_URL!r}; "
                    "archive=zipfile.ZipFile(sys.argv[1]); "
                    "names=[n for n in archive.namelist() "
                    "if n.startswith('agent_runtime/') and not n.endswith('/')]; "
                    "assert len(names)==60; "
                    "assert all(pathlib.Path(d.locate_file(n)).read_bytes()==archive.read(n) "
                    "for n in names); "
                    "print(json.dumps({'version':d.version,'source':source,'python':platform.python_version(),"
                    "'installed_package_files_match_verified_wheel':len(names),"
                    "'platform':platform.platform(),'packages':"
                    "{p.metadata['Name']:p.version for p in m.distributions()}},indent=2))",
                    str(wheel_path),
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
                    "-s",
                    "-p",
                    "no:cacheprovider",
                    "-W",
                    "error::pytest.PytestUnraisableExceptionWarning",
                    f"--basetemp={root / 'pytest-temp'}",
                    f"--junitxml={reports / 'tests.xml'}",
                ]
            )
        manifest["status"] = "passed"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["failure_type"] = type(exc).__name__
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Model-input baseline evidence: {reports}")


if __name__ == "__main__":
    main()
