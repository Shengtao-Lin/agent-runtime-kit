"""Credential-free HTTP smoke test for the containerized example."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, cast
from uuid import uuid4

BASE_URL = "http://127.0.0.1:8000"


def request_json(
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Send one JSON request using only the Python standard library."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    if idempotency_key is not None:
        request.add_header("Idempotency-Key", idempotency_key)
    with urllib.request.urlopen(request, timeout=10) as response:
        return cast(dict[str, Any], json.load(response))


def invocation(text: str, thread_id: str | None = None) -> dict[str, object]:
    """Build a canonical invocation request."""
    body: dict[str, object] = {
        "user_id": "synthetic-smoke-user",
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
    }
    if thread_id is not None:
        body["thread_id"] = thread_id
    return body


def stream_events(path: str, body: dict[str, object]) -> list[str]:
    """Collect SSE event names from one streaming invocation."""
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        lines = response.read().decode().splitlines()
    return [line.removeprefix("event: ") for line in lines if line.startswith("event: ")]


def main() -> None:
    """Exercise health, all frameworks, history, replay, and feedback."""
    assert request_json("GET", "/healthz") == {"status": "ok"}
    assert request_json("GET", "/readyz") == {"status": "ready"}
    first_key = f"smoke-native-{uuid4()}"
    first_body = invocation("Where is DEMO-42?")
    first = request_json(
        "POST",
        "/v1/agents/support-native/invoke",
        first_body,
        idempotency_key=first_key,
    )
    replay = request_json(
        "POST",
        "/v1/agents/support-native/invoke",
        first_body,
        idempotency_key=first_key,
    )
    assert replay == first
    assert first["tool_results"]
    second = request_json(
        "POST",
        "/v1/agents/support-native/invoke",
        invocation("What else can you do?", str(first["thread_id"])),
    )
    assert second["thread_id"] == first["thread_id"]

    for agent_id in ("support-langchain", "support-langgraph"):
        response = request_json(
            "POST",
            f"/v1/agents/{agent_id}/invoke",
            invocation("Hello"),
        )
        assert response["contract_version"] == "v1"

    events = stream_events(
        "/v1/agents/support-native/invoke/stream",
        invocation("Hello from the stream"),
    )
    assert events == ["started", "text_delta", "completed"]

    feedback_key = f"smoke-feedback-{uuid4()}"
    feedback_body: dict[str, object] = {
        "feedback_id": str(uuid4()),
        "idempotency_key": feedback_key,
        "run_id": first["run_id"],
        "target_type": "message",
        "target_id": first["message"]["id"],
        "source": "user",
        "feedback_type": "thumb",
        "value": True,
    }
    feedback = request_json("POST", "/v1/feedback", feedback_body, idempotency_key=feedback_key)
    repeated = request_json("POST", "/v1/feedback", feedback_body, idempotency_key=feedback_key)
    assert repeated["feedback_id"] == feedback["feedback_id"]
    print("Docker smoke test passed")


if __name__ == "__main__":
    main()
