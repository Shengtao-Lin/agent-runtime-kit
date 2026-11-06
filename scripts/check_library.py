"""Check focused source dependencies and distribution contents without optional imports."""

from __future__ import annotations

import argparse
import ast
import zipfile
from pathlib import Path

CORE_FILES = {
    "__init__.py",
    "models.py",
    "errors.py",
    "runtime.py",
    "registry.py",
    "config.py",
    "_json.py",
    "_streams.py",
    "adapters/base.py",
    "models_clients/base.py",
    "memory/base.py",
    "memory/models.py",
    "runs/base.py",
    "runs/models.py",
    "feedback/base.py",
    "feedback/models.py",
}
OPTIONAL = (
    "httpx",
    "sqlalchemy",
    "psycopg",
    "alembic",
    "fastapi",
    "uvicorn",
    "langchain",
    "langchain_core",
    "langgraph",
)
CONCRETE = (
    "agent_runtime.service",
    "agent_runtime.memory.postgres",
    "agent_runtime.memory.tables",
    "agent_runtime.runs.postgres",
    "agent_runtime.feedback.postgres",
    "agent_runtime.adapters.langchain",
    "agent_runtime.adapters.langgraph",
    "agent_runtime.adapters.framework_messages",
    "agent_runtime.adapters.native",
    "agent_runtime.models_clients.openai_compatible",
    "agent_runtime.models_clients.anthropic",
    "agent_runtime.models_clients.gemini",
)


def matches(module: str, prefixes: tuple[str, ...]) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in prefixes)


def dependency_errors(relative: str, source: str) -> list[str]:
    """Inspect static imports, including local and type-checking imports in core contracts."""
    errors: list[str] = []
    parts = ["agent_runtime", *Path(relative).with_suffix("").parts]
    package = parts[:-1]
    core = relative in CORE_FILES or relative.startswith(("hooks/", "tools/"))
    for node in ast.walk(ast.parse(source)):
        imports: list[str] = []
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(
                    [*package[: len(package) - node.level + 1], *([base] if base else [])]
                )
            imports = [base, *(f"{base}.{alias.name}" for alias in node.names)]
        for module in imports:
            if matches(module, ("examples", "applications")) or (
                core and matches(module, OPTIONAL + CONCRETE)
            ):
                errors.append(f"{relative}:{node.lineno}: forbidden dependency {module}")
    return errors


def wheel_errors(names: set[str]) -> list[str]:
    required = {
        "agent_runtime/__init__.py",
        "agent_runtime/py.typed",
        "agent_runtime/migrations/env.py",
        "agent_runtime/migrations/script.py.mako",
        "agent_runtime/migrations/versions/0001_initial_schema.py",
        "agent_runtime/migrations/versions/0002_fenced_thread_ownership.py",
    }
    errors = [f"Missing wheel resource: {name}" for name in sorted(required - names)]
    for name in sorted(names):
        root = name.split("/")[0]
        if root != "agent_runtime" and not root.endswith(".dist-info"):
            errors.append(f"Unexpected wheel root: {root}")
        if "__pycache__" in name or name.endswith((".pyc", ".env")):
            errors.append(f"Unexpected wheel resource: {name}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    package = args.root / "src" / "agent_runtime"
    errors: list[str] = []
    for path in package.rglob("*.py"):
        errors.extend(
            dependency_errors(
                path.relative_to(package).as_posix(), path.read_text(encoding="utf-8")
            )
        )
    if args.wheel:
        with zipfile.ZipFile(args.wheel) as wheel:
            errors.extend(wheel_errors(set(wheel.namelist())))
    if errors:
        raise SystemExit("\n".join(errors))
    print("Library dependency and requested wheel checks passed")


if __name__ == "__main__":
    main()
