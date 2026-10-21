"""Run a downstream pytest project outside the checkout, installed from a wheel."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

BASELINE_SOURCE_URL = (
    "https://github.com/Shengtao-Lin/agent-runtime-kit/releases/download/v0.1.1/"
    "agent_runtime_kit-0.1.1.tar.gz"
)
BASELINE_SOURCE_SHA256 = "648fba0b0da98bf075a98d41f9366a2422e533544af73b727ebe0705cefceeef"


def published_migrations(destination: Path) -> None:
    """Copy only known migration files from the checksum-verified release source archive."""
    with urllib.request.urlopen(BASELINE_SOURCE_URL, timeout=30) as response:
        data = response.read(5_000_001)
    if len(data) > 5_000_000 or hashlib.sha256(data).hexdigest() != BASELINE_SOURCE_SHA256:
        raise ValueError("Published baseline source archive failed size or checksum verification")
    resources = (
        "alembic.ini",
        "alembic/env.py",
        "alembic/script.py.mako",
        "alembic/versions/0001_initial_schema.py",
    )
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for resource in resources:
            member = archive.getmember(f"agent_runtime_kit-0.1.1/{resource}")
            if not member.isfile():
                raise ValueError("Migration resource is not a regular file")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Migration resource is missing")
            target = destination / resource
            target.parent.mkdir(parents=True, exist_ok=True)
            with stream:
                target.write_bytes(stream.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel", type=Path, help="Override the published baseline with a built wheel"
    )
    parser.add_argument("--expected-version", default="0.1.1")
    parser.add_argument("--expected-revision", default="0001")
    parser.add_argument(
        "--upgrade-from-baseline",
        action="store_true",
        help="Seed published v0.1.1 records before installing and migrating a candidate wheel",
    )
    parser.add_argument("--acceptance-only", action="store_true", help="Exclude v0.2.0 gap probes")
    parser.add_argument("--report-dir", type=Path, default=Path("build/consumer-validation"))
    args = parser.parse_args()
    database_url = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not database_url:
        parser.error("Set AGENT_RUNTIME_TEST_DATABASE_URL to a disposable PostgreSQL database")
    if args.upgrade_from_baseline and not args.wheel:
        parser.error("--upgrade-from-baseline requires --wheel")
    repository = Path(__file__).resolve().parents[1]
    report_directory = args.report_dir.resolve()
    report_directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["DATABASE_URL"] = database_url
    environment["CONSUMER_EXPECTED_VERSION"] = args.expected_version
    environment["CONSUMER_EXPECTED_REVISION"] = args.expected_revision
    environment["CONSUMER_PACKAGED_MIGRATIONS"] = "1" if args.wheel else "0"
    environment["CONSUMER_CANDIDATE"] = "1" if args.wheel else "0"

    def run(command: list[str], *, cwd: Path) -> None:
        subprocess.run(command, cwd=cwd, env=environment, check=True)

    with tempfile.TemporaryDirectory(prefix="runtime-kit-consumer-") as temporary:
        root = Path(temporary)
        consumer = root / "consumer"
        shutil.copytree(
            repository / "validation" / "consumer",
            consumer,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
        )
        run(["uv", "sync", "--locked"], cwd=consumer)
        python = (
            consumer / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        )
        migration = root / "migration-job"
        migration.mkdir()
        if not args.wheel or args.upgrade_from_baseline:
            published_migrations(migration)
            run([str(python), "-m", "alembic", "upgrade", "head"], cwd=migration)
        if args.upgrade_from_baseline:
            upgrade_state = root / "upgrade-state.json"
            environment["CONSUMER_UPGRADE_STATE"] = str(upgrade_state)
            run([str(python), "seed_upgrade.py"], cwd=consumer)
        if args.wheel:
            wheel = args.wheel.resolve(strict=True)
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
                    str(wheel),
                ],
                cwd=consumer,
            )
        run(["uv", "pip", "check", "--python", str(python)], cwd=consumer)

        if args.wheel:
            # Candidate schema setup uses only the installed wheel from an empty directory.
            run([str(python), "-m", "agent_runtime.migrations", "upgrade"], cwd=root)
            run([str(python), "-m", "agent_runtime.migrations", "upgrade"], cwd=root)
            run([str(python), "-m", "agent_runtime.migrations", "current"], cwd=root)
        inventory = subprocess.run(
            [
                str(python),
                "-c",
                "import importlib.metadata,json; print(json.dumps({"
                "d.metadata['Name']:d.version for d in importlib.metadata.distributions()},"
                "sort_keys=True,indent=2))",
            ],
            cwd=consumer,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        (report_directory / "installed-packages.json").write_text(
            inventory.stdout, encoding="utf-8"
        )
        audit = subprocess.run(
            [
                str(python),
                "-c",
                "import importlib.metadata,json,platform; "
                "d=importlib.metadata.distribution('agent-runtime-kit'); "
                "print(json.dumps({'version':d.version,'python':platform.python_version(),"
                "'platform':platform.system(),"
                "'source':json.loads(d.read_text('direct_url.json') or '{}'),"
                "'migration_resources':[str(f) for f in d.files or [] "
                "if 'alembic' in str(f) or 'migrations' in str(f)]},indent=2))",
            ],
            cwd=consumer,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        package_audit = json.loads(audit.stdout)
        package_audit["migration_source"] = (
            "installed-wheel"
            if args.wheel
            else {"url": BASELINE_SOURCE_URL, "sha256": BASELINE_SOURCE_SHA256}
        )
        (report_directory / "package-audit.json").write_text(
            json.dumps(package_audit, indent=2) + "\n", encoding="utf-8"
        )
        command = [
            str(python),
            "-m",
            "pytest",
            "-q",
            "-rxX",
            "-p",
            "no:cacheprovider",
            f"--junitxml={report_directory / 'results.xml'}",
            f"--basetemp={root / 'pytest-temp'}",
        ]
        if args.acceptance_only:
            command.extend(["-m", "not readiness"])
        run(command, cwd=consumer)
    print(f"Consumer reports: {report_directory}")


if __name__ == "__main__":
    main()
