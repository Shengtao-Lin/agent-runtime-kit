"""Check an installed core-only wheel in a fresh external venv and process."""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from check_library import wheel_errors


def main() -> None:
    import zipfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=Path("build/core-imports"))
    args = parser.parse_args()
    wheels = list(args.wheel_dir.resolve().glob("agent_runtime_kit-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one candidate wheel")
    with zipfile.ZipFile(wheels[0]) as archive:
        errors = wheel_errors(set(archive.namelist()))
    if errors:
        raise SystemExit("\n".join(errors))
    checkout = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="runtime-kit-core-") as temporary:
        root = Path(temporary)
        environment = root / ".venv"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheels[0])], check=True
        )
        subprocess.run(["uv", "pip", "check", "--python", str(python)], check=True)
        probe = root / "probe_core_imports.py"
        shutil.copy2(checkout / "scripts" / probe.name, probe)
        result = subprocess.run(
            [str(python), "-I", str(probe), str(checkout)],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise SystemExit(result.stderr)
        report = json.loads(result.stdout)
        report["wheel"] = str(wheels[0])
        args.report_dir.mkdir(parents=True, exist_ok=True)
        (args.report_dir / "core-imports.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
