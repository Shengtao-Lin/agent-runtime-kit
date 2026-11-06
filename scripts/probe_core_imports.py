"""Run from a core-only installed-wheel venv outside the source checkout."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.metadata
import json
import os
import sys
import threading
from pathlib import Path

FORBIDDEN = (
    "httpx",
    "sqlalchemy",
    "psycopg",
    "alembic",
    "fastapi",
    "uvicorn",
    "langchain",
    "langchain_core",
    "langgraph",
    "opentelemetry.sdk",
    "opentelemetry.exporter",
    "agent_runtime.service",
    "agent_runtime.memory.postgres",
    "agent_runtime.memory.tables",
    "agent_runtime.runs.postgres",
    "agent_runtime.feedback.postgres",
    "agent_runtime.adapters.langchain",
    "agent_runtime.adapters.langgraph",
    "agent_runtime.adapters.native",
    "agent_runtime.adapters.framework_messages",
)


class ForbiddenImport(ImportError):
    pass


class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in FORBIDDEN):
            raise ForbiddenImport(f"Core import loaded optional implementation: {fullname}")
        return None


def audit(event, args):
    if event in {"socket.connect", "socket.bind", "subprocess.Popen"}:
        raise RuntimeError(f"Import started external work: {event}")
    if (
        event == "open"
        and isinstance(args[0], (str, bytes))
        and Path(os.fsdecode(args[0])).name == ".env"
    ):
        raise RuntimeError("Import read .env")


def no_thread(*args, **kwargs):
    raise RuntimeError("Import started a thread")


def main():
    checkout = Path(sys.argv[1]).resolve()
    sys.meta_path.insert(0, Guard())
    sys.addaudithook(audit)
    threading.Thread.start = no_thread
    for module in (
        "agent_runtime",
        "agent_runtime.config",
        "agent_runtime.hooks",
        "agent_runtime.tools",
        "agent_runtime.memory",
        "agent_runtime.runs",
        "agent_runtime.feedback",
        "agent_runtime.models_clients",
        "agent_runtime.migrations",
        "agent_runtime.adapters.base",
    ):
        importlib.import_module(module)
    import agent_runtime

    location = Path(agent_runtime.__file__).resolve()
    assert not location.is_relative_to(checkout), location
    distribution = importlib.metadata.distribution("agent-runtime-kit")
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert not direct.get("dir_info", {}).get("editable"), direct
    for forbidden in FORBIDDEN:
        assert not any(
            name == forbidden or name.startswith(forbidden + ".") for name in sys.modules
        )
        try:
            importlib.import_module(forbidden)
        except ForbiddenImport:
            pass
        else:
            raise AssertionError(f"Import guard did not reject {forbidden}")
    print(
        json.dumps(
            {
                "version": distribution.version,
                "module_path": str(location),
                "blocked_imports": len(FORBIDDEN),
            }
        )
    )


if __name__ == "__main__":
    main()
