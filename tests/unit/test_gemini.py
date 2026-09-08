from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from agent_runtime.models import Message, RuntimeContext, TextContent, ToolCallContent
from agent_runtime.models_clients.gemini import GeminiModelClient


def context() -> RuntimeContext:
    return RuntimeContext(run_id=uuid4(), thread_id=uuid4())


@pytest.mark.asyncio
async def test_gemini_response_and_tool_translation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-secret"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Checking"},
                                {"functionCall": {"name": "lookup", "args": {"id": "42"}}},
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gemini.invalid"
    )
    client = GeminiModelClient(model="gemini-test", api_key="test-secret", client=http_client)
    result = await client.generate(
        [Message(role="user", content=[TextContent(text="Find it")])], context=context()
    )
    assert result.tool_calls[0].name == "lookup"
    assert isinstance(result.message.content[1], ToolCallContent)
    assert result.usage is not None and result.usage.input_tokens == 3
    await http_client.aclose()


@pytest.mark.asyncio
async def test_gemini_stream_translation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["alt"] == "sse"
        assert request.headers["x-goog-api-key"] == "test-secret"
        chunks = [
            {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]},
            {
                "candidates": [{"content": {"parts": [{"text": "lo"}]}}],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
            },
        ]
        content = "\n".join(f"data: {json.dumps(chunk)}" for chunk in chunks)
        return httpx.Response(200, text=content)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://gemini.invalid"
    )
    client = GeminiModelClient(model="gemini-test", api_key="test-secret", client=http_client)
    events = [
        event
        async for event in client.stream(
            [Message(role="user", content=[TextContent(text="Hi")])], context=context()
        )
    ]
    assert [event.type for event in events] == ["text_delta", "text_delta", "completed"]
    completed = events[-1]
    assert completed.type == "completed"
    text = completed.result.message.content[0]
    assert isinstance(text, TextContent) and text.text == "Hello"
    await http_client.aclose()
