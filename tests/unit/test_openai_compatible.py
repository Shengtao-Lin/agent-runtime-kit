from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from agent_runtime.errors import ProviderError
from agent_runtime.models import Message, RuntimeContext, TextContent
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient


def context() -> RuntimeContext:
    return RuntimeContext(run_id=uuid4(), thread_id=uuid4())


@pytest.mark.asyncio
async def test_openai_compatible_response_translation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "demo-model"
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Hello from provider",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3},
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.invalid/v1"
    )
    client = OpenAICompatibleModelClient(
        model="demo-model", api_key="test-secret", client=http_client
    )
    result = await client.generate(
        [Message(role="user", content=[TextContent(text="Hello")])],
        context=context(),
    )
    assert isinstance(result.message.content[0], TextContent)
    assert result.message.content[0].text == "Hello from provider"
    assert result.usage is not None
    assert result.usage.input_tokens == 4
    await http_client.aclose()


@pytest.mark.asyncio
async def test_openai_compatible_invalid_response_is_safe_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"choices": []})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.invalid/v1"
    )
    client = OpenAICompatibleModelClient(
        model="demo-model", api_key="test-secret", client=http_client
    )
    with pytest.raises(ProviderError, match="model request failed"):
        await client.generate(
            [Message(role="user", content=[TextContent(text="Hello")])],
            context=context(),
        )
    await http_client.aclose()


@pytest.mark.asyncio
async def test_openai_compatible_stream_translation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        content = "\n".join(
            [
                'data: {"choices":[{"delta":{"content":"Hel"}}]}',
                'data: {"choices":[{"delta":{"content":"lo"}}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}',
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.invalid/v1"
    )
    client = OpenAICompatibleModelClient(
        model="demo-model", api_key="test-secret", client=http_client
    )
    events = [
        event
        async for event in client.stream(
            [Message(role="user", content=[TextContent(text="Hello")])],
            context=context(),
        )
    ]
    assert [event.type for event in events] == ["text_delta", "text_delta", "completed"]
    completed = events[-1]
    assert completed.type == "completed"
    text = completed.result.message.content[0]
    assert isinstance(text, TextContent)
    assert text.text == "Hello"
    assert completed.result.usage is not None
    assert completed.result.usage.input_tokens == 2
    await http_client.aclose()
