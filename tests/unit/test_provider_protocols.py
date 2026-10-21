"""Provider wire failures must never become successful canonical completions."""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from agent_runtime.errors import ProviderError
from agent_runtime.models import Message, RuntimeContext, TextContent
from agent_runtime.models_clients.anthropic import AnthropicModelClient
from agent_runtime.models_clients.gemini import GeminiModelClient
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
@pytest.mark.parametrize("failure", ["eof", "error"])
async def test_partial_provider_stream_is_not_completed(provider: str, failure: str) -> None:
    partial: dict[str, object]
    if provider == "openai":
        partial = {"choices": [{"delta": {"content": "provisional"}}]}
    elif provider == "anthropic":
        partial = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "provisional"},
        }
    else:
        partial = {"candidates": [{"content": {"parts": [{"text": "provisional"}]}}]}
    events = [partial]
    if failure == "error":
        events.append({"type": "error", "error": {"message": "PRIVATE-SENTINEL"}})
    body = "\n".join(f"data: {json.dumps(event)}\n" for event in events)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
        base_url="https://provider.invalid",
    ) as transport:
        clients = {
            "openai": OpenAICompatibleModelClient,
            "anthropic": AnthropicModelClient,
            "gemini": GeminiModelClient,
        }
        client = clients[provider](model="test-model", api_key="synthetic", client=transport)
        observed: list[str] = []
        with pytest.raises(ProviderError) as captured:
            async for event in client.stream(
                [Message(role="user", content=[TextContent(text="Hello")])],
                context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
            ):
                observed.append(event.type)
        assert "completed" not in observed
        assert "PRIVATE-SENTINEL" not in str(captured.value)


@pytest.mark.parametrize(
    ("provider", "body"),
    [
        (
            "openai",
            'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n'
            "data: [DONE]\n\n",
        ),
        (
            "anthropic",
            'data: {"type":"content_block_delta","index":0,'
            '"delta":{"type":"text_delta","text":"partial"}}\n\n'
            'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"}}\n\n'
            'data: {"type":"message_stop"}\n\n',
        ),
        (
            "gemini",
            'data: {"candidates":[{"content":{"parts":[{"text":"partial"}]},'
            '"finishReason":"SAFETY"}]}\n\n',
        ),
    ],
)
async def test_unsuccessful_finish_is_not_completed(provider: str, body: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
        base_url="https://provider.invalid",
    ) as transport:
        clients = {
            "openai": OpenAICompatibleModelClient,
            "anthropic": AnthropicModelClient,
            "gemini": GeminiModelClient,
        }
        client = clients[provider](model="test-model", api_key="synthetic", client=transport)
        with pytest.raises(ProviderError):
            async for event in client.stream(
                [Message(role="user", content=[TextContent(text="Hello")])],
                context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
            ):
                assert event.type != "completed"


async def test_gemini_finish_only_frame_completes_existing_text() -> None:
    body = (
        'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}\n\n'
        'data: {"candidates":[{"finishReason":"STOP"}],'
        '"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":1}}\n\n'
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
        base_url="https://provider.invalid",
    ) as transport:
        client = GeminiModelClient(model="test-model", api_key="synthetic", client=transport)
        events = [
            event
            async for event in client.stream(
                [Message(role="user", content=[TextContent(text="Hello")])],
                context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
            )
        ]
        assert [event.type for event in events] == ["text_delta", "completed"]
        completed = events[-1]
        assert completed.type == "completed"
        assert completed.result.usage is not None
        assert completed.result.usage.output_tokens == 1
