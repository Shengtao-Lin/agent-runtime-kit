from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest

from agent_runtime.models import Message, RuntimeContext, TextContent, ToolCallContent
from agent_runtime.models_clients.anthropic import AnthropicModelClient


def context() -> RuntimeContext:
    return RuntimeContext(run_id=uuid4(), thread_id=uuid4())


@pytest.mark.asyncio
async def test_anthropic_response_and_tool_translation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 256
        assert request.headers["x-api-key"] == "test-secret"
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Checking"},
                    {
                        "type": "tool_use",
                        "id": "call-1",
                        "name": "lookup",
                        "input": {"id": "DEMO-42"},
                    },
                ],
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        )

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.anthropic.invalid"
    )
    client = AnthropicModelClient(
        model="claude-test", api_key="test-secret", max_tokens=256, client=http_client
    )
    result = await client.generate(
        [Message(role="user", content=[TextContent(text="Find it")])], context=context()
    )
    assert result.tool_calls[0].name == "lookup"
    assert isinstance(result.message.content[1], ToolCallContent)
    assert result.usage is not None and result.usage.output_tokens == 3
    await http_client.aclose()


@pytest.mark.asyncio
async def test_anthropic_stream_translation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello"},
            },
            {"type": "message_delta", "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        content = "\n".join(f"data: {json.dumps(event)}" for event in events)
        return httpx.Response(200, text=content)

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.anthropic.invalid"
    )
    client = AnthropicModelClient(model="claude-test", api_key="test-secret", client=http_client)
    events = [
        event
        async for event in client.stream(
            [Message(role="user", content=[TextContent(text="Hi")])], context=context()
        )
    ]
    assert [event.type for event in events] == ["text_delta", "completed"]
    completed = events[-1]
    assert completed.type == "completed"
    assert completed.result.usage is not None
    assert completed.result.usage.input_tokens == 4
    await http_client.aclose()
