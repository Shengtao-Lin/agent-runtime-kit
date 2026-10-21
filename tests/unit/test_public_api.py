"""Require explicit review when documented runtime/provider signatures change."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from agent_runtime import AgentRuntime
from agent_runtime.errors import ProviderError
from agent_runtime.models_clients.anthropic import AnthropicModelClient
from agent_runtime.models_clients.gemini import GeminiModelClient
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient


def signature_snapshot() -> dict[str, list[dict[str, Any]]]:
    targets = {
        "AgentRuntime": AgentRuntime,
        "AgentRuntime.invoke": AgentRuntime.invoke,
        "AgentRuntime.stream": AgentRuntime.stream,
        "AgentRuntime.shutdown": AgentRuntime.shutdown,
        "AgentRuntime.lifespan": AgentRuntime.lifespan,
        "ProviderError": ProviderError,
        "OpenAICompatibleModelClient": OpenAICompatibleModelClient,
        "AnthropicModelClient": AnthropicModelClient,
        "GeminiModelClient": GeminiModelClient,
    }
    return {
        name: [
            {
                "name": parameter.name,
                "kind": parameter.kind.name,
                "default": (
                    "<required>"
                    if parameter.default is inspect.Parameter.empty
                    else parameter.default
                ),
            }
            for parameter in inspect.signature(target).parameters.values()
        ]
        for name, target in targets.items()
    }


def test_documented_signatures_match_reviewed_snapshot() -> None:
    expected = Path(__file__).parents[1] / "snapshots" / "python_api.json"
    assert signature_snapshot() == json.loads(expected.read_text(encoding="utf-8"))
