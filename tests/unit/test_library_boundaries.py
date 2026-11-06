"""Architectural guards reject representative violations rather than only the current tree."""

from pathlib import Path

import pytest

from scripts.check_library import dependency_errors, wheel_errors


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy",
        "from agent_runtime.memory import postgres",
        "from .memory.postgres import PostgresMemoryStore",
        "from examples.support_agent import agent",
        "def helper():\n    import httpx",
        "from agent_runtime.adapters.langchain import LangChainRunnableAdapter",
    ],
)
def test_core_dependency_guard_rejects_violations(source: str) -> None:
    assert dependency_errors("runtime.py", source)


def test_current_source_obeys_dependency_direction() -> None:
    package = Path(__file__).parents[2] / "src" / "agent_runtime"
    errors = [
        error
        for path in package.rglob("*.py")
        for error in dependency_errors(
            path.relative_to(package).as_posix(), path.read_text(encoding="utf-8")
        )
    ]
    assert not errors, errors


def test_optional_implementation_may_import_its_dependency() -> None:
    assert not dependency_errors("memory/postgres.py", "from sqlalchemy import select")


def test_distribution_guard_rejects_missing_resources_and_application_code() -> None:
    errors = wheel_errors({"agent_runtime/__init__.py", "applications/release_desk/app.py"})
    assert any("py.typed" in error for error in errors)
    assert any("0002" in error for error in errors)
    assert any("applications" in error for error in errors)
