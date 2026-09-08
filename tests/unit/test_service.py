from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi.testclient import TestClient

from agent_runtime import AgentRegistry
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.errors import UnknownAgentError
from agent_runtime.feedback.base import FeedbackStore
from agent_runtime.feedback.models import FeedbackRecord, FeedbackSubmission
from agent_runtime.models import (
    AgentDescriptor,
    Message,
    RuntimeRequest,
    RuntimeResponse,
    TextContent,
)
from agent_runtime.models_clients import DeterministicModelClient, ModelResult
from agent_runtime.runtime import AgentRuntime
from agent_runtime.service import create_app


class FakeRuntime:
    def __init__(self, descriptor: AgentDescriptor) -> None:
        self.descriptor = descriptor
        self.last_idempotency_key: str | None = None

    async def invoke(
        self,
        agent_id: str,
        request: RuntimeRequest,
        *,
        idempotency_key: str | None = None,
    ) -> RuntimeResponse:
        if agent_id != self.descriptor.agent_id:
            raise UnknownAgentError(f"Agent '{agent_id}' is not registered")
        self.last_idempotency_key = idempotency_key
        return RuntimeResponse(
            run_id=uuid4(),
            request_id=request.request_id,
            thread_id=request.thread_id or uuid4(),
            agent=self.descriptor,
            message=Message(role="assistant", content=[TextContent(text="Fake response")]),
        )


class FakeFeedbackStore:
    async def record(self, submission: FeedbackSubmission) -> FeedbackRecord:
        return FeedbackRecord(
            **submission.model_dump(),
            payload_hash="fake-hash",
            created_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
        )


class FakeReadiness:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready

    async def check_ready(self) -> bool:
        return self.ready

    async def check_migrations(self, expected_revision: str) -> bool:
        return self.ready and expected_revision == "0001"


def build_client(
    *, ready: bool = True, max_bytes: int = 1_000_000
) -> tuple[TestClient, FakeRuntime]:
    descriptor = AgentDescriptor(agent_id="test-agent", version="1", framework="native")
    model = DeterministicModelClient(
        [ModelResult(message=Message(role="assistant", content=[TextContent(text="unused")]))]
    )
    registry = AgentRegistry()
    registry.register(
        NativeAgentInvoker(
            agent_id=descriptor.agent_id,
            version=descriptor.version,
            model_client=model,
        )
    )
    runtime = FakeRuntime(descriptor)
    app = create_app(
        runtime=cast(AgentRuntime, runtime),
        agents=registry,
        feedback=cast(FeedbackStore, FakeFeedbackStore()),
        readiness=FakeReadiness(ready),
        max_request_body_bytes=max_bytes,
    )
    return TestClient(app, raise_server_exceptions=False), runtime


def invocation_body() -> dict[str, object]:
    return {"messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]}


def test_health_readiness_discovery_and_invocation_contract() -> None:
    client, runtime = build_client()
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/v1/agents").json()["agents"][0]["agent_id"] == "test-agent"

    response = client.post(
        "/v1/agents/test-agent/invoke",
        headers={"Idempotency-Key": "invoke-1"},
        json=invocation_body(),
    )
    assert response.status_code == 200
    assert response.json()["contract_version"] == "v1"
    assert runtime.last_idempotency_key == "invoke-1"


def test_readiness_and_errors_use_documented_statuses_and_envelope() -> None:
    client, _ = build_client(ready=False)
    assert client.get("/readyz").status_code == 503
    response = client.post("/v1/agents/missing/invoke", json=invocation_body())
    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "unknown_agent",
        "message": "Agent 'missing' is not registered",
        "retryable": False,
    }


def test_feedback_requires_matching_idempotency_header() -> None:
    client, _ = build_client()
    run_id = uuid4()
    body = {
        "feedback_id": str(uuid4()),
        "idempotency_key": "feedback-1",
        "run_id": str(run_id),
        "target_type": "run",
        "target_id": str(run_id),
        "source": "user",
        "feedback_type": "thumb",
        "value": True,
    }
    mismatch = client.post("/v1/feedback", headers={"Idempotency-Key": "different"}, json=body)
    assert mismatch.status_code == 409
    recorded = client.post("/v1/feedback", headers={"Idempotency-Key": "feedback-1"}, json=body)
    assert recorded.status_code == 201
    assert recorded.json()["run_id"] == str(run_id)


def test_request_body_limit_is_enforced() -> None:
    client, _ = build_client(max_bytes=1_024)
    response = client.post(
        "/v1/agents/test-agent/invoke",
        content="x" * 1_025,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_openapi_contract_snapshot() -> None:
    client, _ = build_client()
    document = client.get("/openapi.json").json()
    projection = {
        "paths": {
            path: sorted(method for method in item if method != "parameters")
            for path, item in document["paths"].items()
        },
        "schemas": sorted(document["components"]["schemas"]),
    }
    snapshot_path = Path(__file__).parents[1] / "snapshots" / "openapi_contract.json"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert projection == expected
