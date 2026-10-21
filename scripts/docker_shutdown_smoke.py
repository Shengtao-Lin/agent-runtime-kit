"""Exercise real SIGTERM with in-flight synthetic work in an isolated test stack.

Recreates/stops this checkout's Compose app; retains PostgreSQL and all volumes.
Run only on a disposable test stack, after building the app image. Uses stdlib only.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "infra/compose.shutdown-test.yml"]
BASE = "http://127.0.0.1:8000"


def command(args: list[str], env: dict[str, str], *, timeout: float = 40) -> str:
    return subprocess.run(
        args, cwd=ROOT, env=env, capture_output=True, text=True, check=True, timeout=timeout
    ).stdout.strip()


def query(sql: str, env: dict[str, str]) -> str:
    return command(
        [
            *COMPOSE,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "agent_runtime",
            "-d",
            "agent_runtime",
            "-At",
            "-c",
            sql,
        ],
        env,
        timeout=10,
    )


def invoke(body: dict[str, object], key: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(
        f"{BASE}/v1/agents/support-native/invoke",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, {}
    except (urllib.error.URLError, ConnectionError):
        return 0, {}


def wait_for_running(request_id: UUID, env: dict[str, str]) -> tuple[str, str]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        row = query(
            f"SELECT id, thread_id, status FROM runtime_runs WHERE request_id = '{request_id}'", env
        )
        if row:
            run_id, thread_id, status = row.split("|")
            logs = command([*COMPOSE, "logs", "--no-color", "app"], env)
            if status == "running" and any(
                run_id in line and '"message":"demo_model_delay_started"' in line
                for line in logs.splitlines()
            ):
                return run_id, thread_id
            if status not in {"accepted", "running"}:
                raise AssertionError(f"Request finished before SIGTERM: {status}")
        time.sleep(0.1)
    raise TimeoutError("Synthetic request did not reach its delayed model")


def case(*, cancel: bool) -> tuple[dict[str, object], str, dict[str, Any]]:
    env = {
        **os.environ,
        "DEMO_MODEL_DELAY_SECONDS": "10" if cancel else "1",
        "SHUTDOWN_GRACE_SECONDS": "0.5" if cancel else "5",
    }
    command(
        [*COMPOSE, "up", "--no-deps", "-d", "--wait", "--force-recreate", "app"], env, timeout=90
    )
    request_id = uuid4()
    key = f"shutdown-{request_id}"
    body: dict[str, object] = {
        "request_id": str(request_id),
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Where is DEMO-42?"}]}],
    }
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(invoke, body, key)
        run_id, thread_id = wait_for_running(request_id, env)
        started = time.monotonic()
        command([*COMPOSE, "stop", "-t", "15", "app"], env, timeout=25)
        assert time.monotonic() - started < 20, "Container exceeded shutdown envelope"
        status, response = future.result(timeout=5)
    container = command([*COMPOSE, "ps", "-a", "-q", "app"], env)
    assert (
        command(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}|{{.State.OOMKilled}}", container], env
        )
        == "0|false"
    )
    row = query(
        "SELECT status, COALESCE(error_code, ''), response IS NOT NULL "
        f"FROM runtime_runs WHERE id = '{UUID(run_id)}'",
        env,
    )
    count = int(
        query(f"SELECT count(*) FROM agent_messages WHERE thread_id = '{UUID(thread_id)}'", env)
    )
    if cancel:
        assert row == "failed|invocation_cancelled|f" and count == 0, (row, count)
        assert status != 200
    else:
        assert row == "succeeded||t" and count == 4, (row, count)
        assert status == 200 and str(response["run_id"]) == run_id
    print(f"SIGTERM {'cancellation' if cancel else 'drain'} passed: {row}; messages={count}")
    return body, key, response


def main() -> None:
    env = {**os.environ, "DEMO_MODEL_DELAY_SECONDS": "0", "SHUTDOWN_GRACE_SECONDS": "20"}
    try:
        successful = case(cancel=False)
        cancelled = case(cancel=True)
        command(
            [*COMPOSE, "up", "--no-deps", "-d", "--wait", "--force-recreate", "app"],
            env,
            timeout=90,
        )
        status, replay = invoke(successful[0], successful[1])
        assert status == 200 and replay == successful[2]
        assert invoke(cancelled[0], cancelled[1])[0] == 409
        print("Post-restart success replay and cancelled-key rejection passed")
    finally:
        command([*COMPOSE, "stop", "-t", "15", "app"], env, timeout=25)


if __name__ == "__main__":
    main()
