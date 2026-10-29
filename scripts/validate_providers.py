"""Verify provider and native-model contracts from an installed wheel outside the checkout."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--report-dir", type=Path, default=Path("build/provider-wheel"))
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    repository = Path(__file__).resolve().parents[1]
    reports = args.report_dir.resolve()
    reports.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    with tempfile.TemporaryDirectory(prefix="runtime-kit-providers-") as temporary:
        root = Path(temporary)
        python = (
            root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        )

        def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                command, cwd=root, env=environment, text=True, capture_output=capture, check=True
            )

        run(["uv", "venv", "--python", sys.executable, str(root / ".venv")])
        run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                f"{wheel}[openai-compatible,anthropic,gemini]",
                "pytest==8.4.2",
                "pytest-asyncio==0.26.0",
            ]
        )
        run(["uv", "pip", "check", "--python", str(python)])
        for name in (
            "test_openai_compatible.py",
            "test_anthropic.py",
            "test_gemini.py",
            "test_provider_protocols.py",
            "test_provider_contracts.py",
            "test_provider_tool_contracts.py",
            "test_native_usage.py",
            "test_model_stream_contracts.py",
            "test_model_result_contracts.py",
            "test_tool_payloads.py",
        ):
            shutil.copy2(repository / "tests" / "unit" / name, root / name)
        audit = run(
            [
                str(python),
                "-c",
                "import importlib.metadata as m, json, pathlib, sys, agent_runtime; "
                "d=m.distribution('agent-runtime-kit'); "
                "p=pathlib.Path(agent_runtime.__file__).resolve(); "
                "assert d.version == sys.argv[1]; "
                "assert not p.is_relative_to(pathlib.Path(sys.argv[2])); "
                "direct=json.loads(d.read_text('direct_url.json') or '{}'); "
                "assert not direct.get('dir_info',{}).get('editable'); "
                "print(json.dumps({'version':d.version,'module_path':str(p),'direct_url':direct}))",
                args.expected_version,
                str(repository),
            ],
            capture=True,
        )
        (reports / "package-audit.json").write_text(audit.stdout, encoding="utf-8")
        inventory = run(["uv", "pip", "freeze", "--python", str(python)], capture=True)
        (reports / "installed-packages.txt").write_text(inventory.stdout, encoding="utf-8")
        run(
            [
                str(python),
                "-m",
                "pytest",
                "-q",
                "-o",
                "asyncio_mode=auto",
                "--junitxml",
                str(reports / "results.xml"),
            ]
        )
    print(f"Provider wheel reports: {reports}")


if __name__ == "__main__":
    main()
