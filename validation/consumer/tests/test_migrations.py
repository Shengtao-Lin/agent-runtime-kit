"""Exercise installed migration entry points and persistence across a wheel upgrade."""

import asyncio
import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from consumer_app import application, request

from agent_runtime import RuntimeResponse

pytestmark = pytest.mark.skipif(
    os.environ.get("CONSUMER_PACKAGED_MIGRATIONS") != "1",
    reason="Published v0.1.1 baseline has no packaged migration entry point",
)


def test_installed_cli_and_resources(tmp_path: Path) -> None:
    resources = files("agent_runtime").joinpath("migrations")
    assert resources.joinpath("env.py").is_file()
    assert resources.joinpath("script.py.mako").is_file()
    assert resources.joinpath("versions", "0001_initial_schema.py").is_file()
    executable = Path(sys.executable).with_name(
        "agent-runtime-migrate.exe" if sys.platform == "win32" else "agent-runtime-migrate"
    )
    result = subprocess.run(
        [str(executable), "heads"], cwd=tmp_path, capture_output=True, text=True, check=True
    )
    assert f"{os.environ['CONSUMER_EXPECTED_REVISION']} (head)" in result.stdout
    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)
    missing = subprocess.run(
        [str(executable), "upgrade"], cwd=tmp_path, env=environment, capture_output=True, text=True
    )
    assert missing.returncode == 2
    assert "DATABASE_URL is required" in missing.stderr


@pytest.mark.asyncio
async def test_upgrade_preserves_published_records(database_url: str) -> None:
    state_path = os.environ.get("CONSUMER_UPGRADE_STATE")
    if not state_path:
        pytest.skip("Only --upgrade-from-baseline seeds v0.1.1 records")
    state = json.loads(await asyncio.to_thread(Path(state_path).read_text, encoding="utf-8"))
    first = RuntimeResponse.model_validate(state["response"])
    async with application(database_url, state["identity"]) as app:
        replay = await app.runtime.invoke(
            app.agent_id, request("double 7"), idempotency_key="upgrade"
        )
        assert replay.model_dump(mode="json") == first.model_dump(mode="json")
        assert app.model.calls == []
        assert len(await app.memory.get_messages(first.thread_id)) == 4
        feedback = await app.feedback.search(run_id=first.run_id)
        assert len(feedback.items) == 1
        assert str(feedback.items[0].feedback_id) == state["feedback_id"]
        memory = await app.memory.get(
            namespace=app.agent_id, user_key="synthetic", memory_key="language"
        )
        assert memory is not None and memory.value == {"language": "en"}
        from uuid import UUID

        legacy = await app.runs.get(UUID(state["legacy_pending_id"]))
        assert legacy is not None and legacy.status == "failed"
        assert legacy.error_code == "legacy_owner_unavailable" and legacy.response is None
