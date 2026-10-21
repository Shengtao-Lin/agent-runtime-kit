"""Offline transport bounds, framing, cleanup and safe retry-hint coverage."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest

from agent_runtime.errors import ProviderError
from agent_runtime.models import Message, RuntimeContext, TextContent
from agent_runtime.models_clients import _http
from agent_runtime.models_clients.anthropic import AnthropicModelClient
from agent_runtime.models_clients.gemini import GeminiModelClient
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient

CLIENTS = [OpenAICompatibleModelClient, AnthropicModelClient, GeminiModelClient]


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("client_type", CLIENTS)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 409, 429, 500, 503])
async def test_http_error_retry_hints_and_response_cleanup(
    client_type: type[OpenAICompatibleModelClient | AnthropicModelClient | GeminiModelClient],
    stream: bool,
    status: int,
) -> None:
    wire = TrackedStream([b"PRIVATE-PROVIDER-BODY"])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, stream=wire)),
        base_url="https://provider.invalid",
    ) as transport:
        client = client_type(model="test", api_key="synthetic", client=transport)
        messages = [Message(role="user", content=[TextContent(text="Hello")])]
        context = RuntimeContext(run_id=uuid4(), thread_id=uuid4())
        with pytest.raises(ProviderError) as captured:
            if stream:
                _ = [event async for event in client.stream(messages, context=context)]
            else:
                await client.generate(messages, context=context)
        assert captured.value.code == "provider_failure"
        assert captured.value.retryable == (status in (408, 409, 429) or status >= 500)
        assert "PRIVATE" not in str(captured.value)
        assert "synthetic" not in str(captured.value)
        assert wire.closed
        await client.close()
        assert not transport.is_closed  # Injected client still belongs to the application.


async def test_sse_multiline_crlf_and_split_utf8() -> None:
    text = 'data: {"text":\r\ndata: "你好"}\r\n\r\n: heartbeat\r\n\r\n'
    wire = TrackedStream([bytes([value]) for value in text.encode()])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
        base_url="https://provider.invalid",
    ) as client:
        async with client.stream("POST", "/") as response:
            assert [event async for event in _http.sse_data(response)] == ['{"text":\n"你好"}']
    assert wire.closed


@pytest.mark.parametrize("stream", [False, True])
async def test_response_total_size_bound_closes_response(
    monkeypatch: pytest.MonkeyPatch, stream: bool
) -> None:
    monkeypatch.setattr(_http, "MAX_RESPONSE_BYTES", 32)
    wire = TrackedStream([b"x" * 33])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
        base_url="https://provider.invalid",
    ) as client:
        with pytest.raises(ProviderError, match="size limit") as captured:
            if stream:
                async with client.stream("POST", "/") as response:
                    _ = [event async for event in _http.sse_data(response)]
            else:
                await _http.response_json(client, "/", payload={}, headers={})
        assert captured.value.retryable is False
    assert wire.closed


async def test_unterminated_sse_line_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http, "MAX_EVENT_BYTES", 32)
    wire = TrackedStream([b"data: " + b"x" * 33])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
        base_url="https://provider.invalid",
    ) as client:
        with pytest.raises(ProviderError, match="event exceeded"):
            async with client.stream("POST", "/") as response:
                _ = [event async for event in _http.sse_data(response)]
    assert wire.closed


async def test_unterminated_sse_event_is_not_dispatched() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="data: [DONE]\n")),
        base_url="https://provider.invalid",
    ) as client:
        async with client.stream("POST", "/") as response:
            assert [event async for event in _http.sse_data(response)] == []


async def test_small_text_delta_arrives_before_next_network_chunk_and_close_releases_response() -> (
    None
):
    class BlockingStream(TrackedStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
            await asyncio.Event().wait()

    wire = BlockingStream([])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=wire)),
        base_url="https://provider.invalid",
    ) as transport:
        client = OpenAICompatibleModelClient(model="test", api_key="synthetic", client=transport)
        stream = client.stream(
            [Message(role="user", content=[TextContent(text="Hello")])],
            context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        )
        try:
            event = await asyncio.wait_for(anext(stream), timeout=2)
            assert event.type == "text_delta"
        finally:
            await stream.aclose()
        assert wire.closed
        assert not transport.is_closed


@pytest.mark.parametrize("client_type", CLIENTS)
async def test_transport_timeout_has_safe_retry_hint(
    client_type: type[OpenAICompatibleModelClient | AnthropicModelClient | GeminiModelClient],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("PRIVATE-TRANSPORT-DETAIL")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.invalid"
    ) as transport:
        client = client_type(model="test", api_key="synthetic", client=transport)
        with pytest.raises(ProviderError) as captured:
            await client.generate(
                [Message(role="user", content=[TextContent(text="Hello")])],
                context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
            )
        assert captured.value.retryable
        assert "PRIVATE" not in str(captured.value)
