from __future__ import annotations

import os
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agent_runtime.config import RuntimeSettings
from examples.support_agent.api import build_application

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if value is None:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    return value


def test_all_example_agents_share_http_contract_and_feedback_is_idempotent() -> None:
    settings = RuntimeSettings(
        database_url=SecretStr(database_url()),
        fake_model=True,
        otel_exporter_otlp_endpoint=None,
    )
    with TestClient(build_application(settings)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").json() == {"status": "ready"}
        agents = client.get("/v1/agents").json()["agents"]
        assert {agent["framework"] for agent in agents} == {
            "native",
            "langchain",
            "langgraph",
        }

        responses: list[dict[str, Any]] = []
        for agent_id in ["support-native", "support-langchain", "support-langgraph"]:
            response = client.post(
                f"/v1/agents/{agent_id}/invoke",
                headers={"Idempotency-Key": f"{agent_id}-{uuid4()}"},
                json={
                    "user_id": "synthetic-demo-user",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Where is order DEMO-42?",
                                }
                            ],
                        }
                    ],
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["contract_version"] == "v1"
            response_body = cast(dict[str, Any], response.json())
            assert response_body["trace_id"]
            responses.append(response_body)

        native = responses[0]
        assert native["tool_results"][0]["tool_name"] == "lookup_order"
        feedback_key = f"feedback-{uuid4()}"
        feedback_id = str(uuid4())
        feedback_body: dict[str, object] = {
            "feedback_id": feedback_id,
            "idempotency_key": feedback_key,
            "run_id": native["run_id"],
            "target_type": "message",
            "target_id": native["message"]["id"],
            "source": "user",
            "feedback_type": "thumb",
            "value": True,
        }
        first = client.post(
            "/v1/feedback",
            headers={"Idempotency-Key": feedback_key},
            json=feedback_body,
        )
        repeated = client.post(
            "/v1/feedback",
            headers={"Idempotency-Key": feedback_key},
            json=feedback_body,
        )
        assert first.status_code == 201
        assert repeated.status_code == 201
        assert first.json()["feedback_id"] == repeated.json()["feedback_id"]
