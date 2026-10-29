"""Malformed supported tool fields must not become valid canonical calls."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Literal, cast
from uuid import uuid4

import httpx
import pytest

from agent_runtime.errors import ProviderError
from agent_runtime.models import Message, RuntimeContext, TextContent
from agent_runtime.models_clients.anthropic import AnthropicModelClient
from agent_runtime.models_clients.base import ModelResult
from agent_runtime.models_clients.gemini import GeminiModelClient
from agent_runtime.models_clients.openai_compatible import OpenAICompatibleModelClient

Provider = Literal["openai", "anthropic", "gemini"]
PROVIDERS: tuple[Provider, ...] = ("openai", "anthropic", "gemini")


class Wire(httpx.AsyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.data

    async def aclose(self) -> None:
        self.closed = True


async def invoke(
    provider: Provider,
    *,
    streaming: bool,
    body: Mapping[str, object] | None = None,
    frames: Sequence[Mapping[str, object] | str] | None = None,
) -> ModelResult:
    wire = (
        "".join(
            f"data: {json.dumps(frame) if isinstance(frame, dict) else frame}\n\n"
            for frame in frames
        )
        if frames is not None
        else json.dumps(body)
    )
    wire_stream = Wire(wire.encode())
    response = httpx.Response(200, stream=wire_stream)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: response), base_url="https://provider.invalid"
    ) as transport:
        client = {
            "openai": OpenAICompatibleModelClient,
            "anthropic": AnthropicModelClient,
            "gemini": GeminiModelClient,
        }[provider](model="test-model", api_key="synthetic", client=transport)
        messages = [Message(role="user", content=[TextContent(text="Hello")])]
        context = RuntimeContext(run_id=uuid4(), thread_id=uuid4())
        result = None
        try:
            if not streaming:
                return await client.generate(messages, context=context)
            async for event in client.stream(messages, context=context):
                if event.type == "completed":
                    assert result is None
                    result = event.result
            assert result is not None
            return result
        except BaseException:
            assert result is None, "Invalid calls must fail before completion"
            raise
        finally:
            assert response.is_closed and wire_stream.closed
            assert not transport.is_closed


def tool_response(
    provider: Provider, *, streaming: bool, call: dict[str, object]
) -> tuple[dict[str, object], list[dict[str, object] | str]]:
    if provider == "openai":
        body: dict[str, object] = {"choices": [{"message": {"tool_calls": [call]}}]}
        frames: list[dict[str, object] | str] = [
            {"choices": [{"delta": {"tool_calls": [call]}}]},
            "[DONE]",
        ]
    elif provider == "anthropic":
        body = {"content": [call]}
        frames = [
            {"type": "content_block_start", "index": 0, "content_block": call},
            {"type": "message_stop"},
        ]
    else:
        body = {
            "candidates": [{"content": {"parts": [{"functionCall": call}]}, "finishReason": "STOP"}]
        }
        frames = [body]
    return body, frames if streaming else []


def valid_call(provider: Provider) -> dict[str, object]:
    if provider == "openai":
        return {
            "index": 0,
            "id": "call-1",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }
    if provider == "anthropic":
        return {"type": "tool_use", "id": "call-1", "name": "lookup", "input": {}}
    return {"name": "lookup", "args": {}}


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("name", [None, True, 17])
async def test_tool_names_are_not_coerced(
    provider: Provider, streaming: bool, name: object
) -> None:
    call = valid_call(provider)
    target = cast(dict[str, object], call["function"]) if provider == "openai" else call
    target["name"] = name
    body, frames = tool_response(provider, streaming=streaming, call=call)
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("identity", [None, True, 17])
async def test_tool_ids_are_not_coerced(
    provider: Provider, streaming: bool, identity: object
) -> None:
    call = valid_call(provider)
    call["id"] = identity
    body, frames = tool_response(provider, streaming=streaming, call=call)
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("raw_calls", [{}, "PRIVATE-SENTINEL", True])
async def test_openai_malformed_call_collection_is_not_dropped(
    streaming: bool, raw_calls: object
) -> None:
    message = {"content": "Valid text must not hide an invalid tool call", "tool_calls": raw_calls}
    body = {"choices": [{"message": message}]}
    frames: list[dict[str, object] | str] = [{"choices": [{"delta": message}]}, "[DONE]"]
    with pytest.raises(ProviderError) as captured:
        await invoke("openai", streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False
    assert "PRIVATE-SENTINEL" not in str(captured.value)


@pytest.mark.parametrize("streaming", [False, True])
async def test_openai_arguments_must_be_json_text(streaming: bool) -> None:
    call = valid_call("openai")
    cast(dict[str, object], call["function"])["arguments"] = {}
    body, frames = tool_response("openai", streaming=streaming, call=call)
    with pytest.raises(ProviderError) as captured:
        await invoke("openai", streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("index", [-1, "0", 0.0, True])
async def test_stream_tool_indices_are_nonnegative_json_integers(
    provider: Provider, index: object
) -> None:
    call = valid_call(provider)
    body, frames = tool_response(provider, streaming=True, call=call)
    if provider == "openai":
        call["index"] = index
    else:
        cast(dict[str, object], frames[0])["index"] = index
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=True, body=body, frames=frames)
    assert captured.value.retryable is False


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("function", [None, [], "PRIVATE-SENTINEL"])
async def test_gemini_malformed_function_is_not_dropped(streaming: bool, function: object) -> None:
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Hello"}, {"functionCall": function}]},
                "finishReason": "STOP",
            }
        ]
    }
    with pytest.raises(ProviderError) as captured:
        await invoke("gemini", streaming=streaming, body=body, frames=[body] if streaming else None)
    assert captured.value.retryable is False
    assert "PRIVATE-SENTINEL" not in str(captured.value)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_valid_empty_argument_call_still_translates(
    provider: Provider, streaming: bool
) -> None:
    body, frames = tool_response(provider, streaming=streaming, call=valid_call(provider))
    result = await invoke(
        provider, streaming=streaming, body=body, frames=frames if streaming else None
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "lookup"
    assert result.tool_calls[0].arguments == {}


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
async def test_nonfinite_tool_arguments_fail_with_safe_provider_errors(
    provider: Provider, streaming: bool, number: float
) -> None:
    call = valid_call(provider)
    arguments = {"PRIVATE-SENTINEL": [number]}
    if provider == "openai":
        cast(dict[str, object], call["function"])["arguments"] = json.dumps(arguments)
    else:
        call["input" if provider == "anthropic" else "args"] = arguments
    body, frames = tool_response(provider, streaming=streaming, call=call)
    with pytest.raises(ProviderError) as caught:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert caught.value.retryable is False
    assert "PRIVATE-SENTINEL" not in caught.value.public_message


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("arguments", [None, False, [], "PRIVATE-SENTINEL"])
async def test_anthropic_input_is_an_object(streaming: bool, arguments: object) -> None:
    call = valid_call("anthropic")
    call["input"] = arguments
    body, frames = tool_response("anthropic", streaming=streaming, call=call)
    with pytest.raises(ProviderError) as captured:
        await invoke(
            "anthropic", streaming=streaming, body=body, frames=frames if streaming else None
        )
    assert captured.value.retryable is False
    assert "PRIVATE-SENTINEL" not in str(captured.value)


async def test_anthropic_repeated_tool_start_does_not_overwrite_a_call() -> None:
    body, frames = tool_response("anthropic", streaming=True, call=valid_call("anthropic"))
    replacement = valid_call("anthropic")
    replacement["id"] = "call-2"
    frames.insert(1, {"type": "content_block_start", "index": 0, "content_block": replacement})
    with pytest.raises(ProviderError) as captured:
        await invoke("anthropic", streaming=True, body=body, frames=frames)
    assert captured.value.retryable is False


async def test_openai_nullable_stream_placeholders_and_fragmented_arguments_are_preserved() -> None:
    call = valid_call("openai")
    cast(dict[str, object], call["function"])["arguments"] = '{"value":'
    body, frames = tool_response("openai", streaming=True, call=call)
    frames.insert(
        1,
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "id": None, "function": {"name": None, "arguments": "1}"}}
                        ]
                    }
                }
            ]
        },
    )
    frames.insert(
        2, {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": None, "function": None}]}}]}
    )
    frames.insert(3, {"choices": [{"delta": {"tool_calls": None}}]})
    result = await invoke("openai", streaming=True, body=body, frames=frames)
    assert result.tool_calls[0].id == "call-1"
    assert result.tool_calls[0].arguments == {"value": 1}


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_duplicate_tool_ids_fail_before_completion(
    provider: Provider, streaming: bool
) -> None:
    call = valid_call(provider)
    body, frames = tool_response(provider, streaming=streaming, call=call)
    second = valid_call(provider)
    if provider == "openai":
        second["index"] = 1
        body = {"choices": [{"message": {"tool_calls": [call, second]}}]}
        frames = [{"choices": [{"delta": {"tool_calls": [call, second]}}]}, "[DONE]"]
    else:
        body = {"content": [call, second]}
        frames = [
            {"type": "content_block_start", "index": 0, "content_block": call},
            {"type": "content_block_start", "index": 1, "content_block": second},
            {"type": "message_stop"},
        ]
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("partial", [None, 17, {}])
async def test_anthropic_malformed_argument_fragment_is_not_dropped(partial: object) -> None:
    body, frames = tool_response("anthropic", streaming=True, call=valid_call("anthropic"))
    frames.insert(
        1,
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": partial},
        },
    )
    with pytest.raises(ProviderError) as captured:
        await invoke("anthropic", streaming=True, body=body, frames=frames)
    assert captured.value.retryable is False


@pytest.mark.parametrize("function", [17, "PRIVATE-SENTINEL", []])
async def test_openai_malformed_function_fragment_is_not_dropped(function: object) -> None:
    body, frames = tool_response("openai", streaming=True, call=valid_call("openai"))
    frames.insert(1, {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": function}]}}]})
    with pytest.raises(ProviderError) as captured:
        await invoke("openai", streaming=True, body=body, frames=frames)
    assert captured.value.retryable is False
    assert "PRIVATE-SENTINEL" not in str(captured.value)
