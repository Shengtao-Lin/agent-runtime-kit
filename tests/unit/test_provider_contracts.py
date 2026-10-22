"""Direct adapter callers receive canonical results or safe provider failures."""

from __future__ import annotations

import json
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


def response_body(provider: Provider, text: str) -> dict[str, object]:
    if provider == "openai":
        return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
    if provider == "anthropic":
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}


def stream_frames(provider: Provider, text: str) -> list[dict[str, object] | str]:
    if provider == "openai":
        return [{"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}, "[DONE]"]
    if provider == "anthropic":
        return [
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
            {"type": "message_stop"},
        ]
    return [response_body(provider, text)]


def usage_fields(provider: Provider) -> tuple[str, str, str]:
    return {
        "openai": ("usage", "prompt_tokens", "completion_tokens"),
        "anthropic": ("usage", "input_tokens", "output_tokens"),
        "gemini": ("usageMetadata", "promptTokenCount", "candidatesTokenCount"),
    }[provider]


async def invoke(
    provider: Provider,
    *,
    streaming: bool,
    body: dict[str, object] | None = None,
    frames: list[dict[str, object] | str] | None = None,
) -> ModelResult:
    wire = (
        "".join(
            f"data: {json.dumps(frame) if isinstance(frame, dict) else frame}\n\n"
            for frame in frames
        )
        if frames is not None
        else json.dumps(body)
    )
    response = httpx.Response(200, text=wire)
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
                    result = event.result
            assert result is not None
            return result
        except BaseException:
            assert result is None, "A failed stream must not have emitted completion"
            raise
        finally:
            assert response.is_closed
            assert not transport.is_closed


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_oversized_canonical_text_is_safe_failure(
    provider: Provider, streaming: bool
) -> None:
    text = "PRIVATE-SENTINEL" + "x" * 32_000
    with pytest.raises(ProviderError) as captured:
        await invoke(
            provider,
            streaming=streaming,
            body=response_body(provider, text),
            frames=stream_frames(provider, text) if streaming else None,
        )
    assert captured.value.retryable is False
    assert "PRIVATE-SENTINEL" not in str(captured.value)
    assert "PRIVATE-SENTINEL" not in captured.value.public_message


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_too_many_canonical_parts_is_safe_failure(
    provider: Provider, streaming: bool
) -> None:
    calls = [{"id": f"call-{index}", "name": "lookup", "arguments": "{}"} for index in range(65)]
    if provider == "openai":
        raw_calls = [
            {
                "index": index,
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
            for index, call in enumerate(calls)
        ]
        body: dict[str, object] = {"choices": [{"message": {"tool_calls": raw_calls}}]}
        frames: list[dict[str, object] | str] = [
            {"choices": [{"delta": {"tool_calls": raw_calls}}]},
            "[DONE]",
        ]
    elif provider == "anthropic":
        blocks: list[dict[str, object]] = [
            {"type": "tool_use", "id": call["id"], "name": call["name"], "input": {}}
            for call in calls
        ]
        body = {"content": blocks}
        frames = [
            {"type": "content_block_start", "index": index, "content_block": block}
            for index, block in enumerate(blocks)
        ]
        frames.append({"type": "message_stop"})
    else:
        parts: list[dict[str, object]] = [
            {"functionCall": {"name": call["name"], "args": {}}} for call in calls
        ]
        body = {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
        frames = [body]
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "supplied", ["absent", "null", "partial", "output-only", "null-count", "complete", "zero"]
)
async def test_usage_never_invents_missing_counts(
    provider: Provider, streaming: bool, supplied: str
) -> None:
    body = response_body(provider, "Hello")
    frames = stream_frames(provider, "Hello")
    key, input_key, output_key = usage_fields(provider)
    counts: dict[str, object] = {}
    if supplied in ("partial", "null-count", "complete", "zero"):
        counts[input_key] = 0 if supplied == "zero" else 7
    if supplied in ("output-only", "complete", "zero"):
        counts[output_key] = 0 if supplied == "zero" else 3
    if supplied == "null-count":
        counts[output_key] = None
    raw_usage = None if supplied == "null" else counts
    if supplied != "absent":
        body[key] = raw_usage
        if provider == "anthropic":
            frames.insert(0, {"type": "message_start", "message": {"usage": raw_usage}})
            frames.insert(-1, {"type": "message_delta", "usage": raw_usage})
        else:
            frame = frames[0]
            assert isinstance(frame, dict)
            frame[key] = raw_usage
    result = await invoke(
        provider, streaming=streaming, body=body, frames=frames if streaming else None
    )
    if supplied not in ("complete", "zero"):
        assert result.usage is None
    else:
        assert result.usage is not None
        assert result.usage.input_tokens == counts[input_key]
        assert result.usage.output_tokens == counts[output_key]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("bad_count", [-1, True, 1.5, "7"])
async def test_invalid_usage_is_safe_failure(
    provider: Provider, streaming: bool, bad_count: object
) -> None:
    body = response_body(provider, "Hello")
    frames = stream_frames(provider, "Hello")
    key, input_key, output_key = usage_fields(provider)
    counts = {input_key: bad_count, output_key: 3}
    body[key] = counts
    if provider == "anthropic":
        frames.insert(0, {"type": "message_start", "message": {"usage": counts}})
    else:
        frame = frames[0]
        assert isinstance(frame, dict)
        frame[key] = counts
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_multiple_candidates_are_not_merged_or_silently_dropped(
    provider: Provider, streaming: bool
) -> None:
    body = response_body(provider, "first")
    frames = stream_frames(provider, "first")
    key = "choices" if provider == "openai" else "candidates"
    target = frames[0] if streaming else body
    assert isinstance(target, dict)
    candidates = target[key]
    assert isinstance(candidates, list)
    typed_candidates = cast(list[object], candidates)
    typed_candidates.append(typed_candidates[0])
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_nonzero_candidate_index_is_rejected(provider: Provider, streaming: bool) -> None:
    body = response_body(provider, "first")
    frames = stream_frames(provider, "first")
    target = frames[0] if streaming else body
    assert isinstance(target, dict)
    choices = cast(
        list[dict[str, object]], target["choices" if provider == "openai" else "candidates"]
    )
    choices[0]["index"] = 1
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
async def test_canonical_text_at_limit_is_accepted(provider: Provider, streaming: bool) -> None:
    text = "x" * 32_000
    result = await invoke(
        provider,
        streaming=streaming,
        body=response_body(provider, text),
        frames=stream_frames(provider, text) if streaming else None,
    )
    part = result.message.content[0]
    assert part.type == "text"
    assert part.text == text


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("raw_usage", [False, [], "PRIVATE-SENTINEL"])
async def test_nonobject_usage_is_rejected(
    provider: Provider, streaming: bool, raw_usage: object
) -> None:
    body = response_body(provider, "Hello")
    frames = stream_frames(provider, "Hello")
    key, _, _ = usage_fields(provider)
    body[key] = raw_usage
    if provider == "anthropic":
        frames.insert(0, {"type": "message_start", "message": {"usage": raw_usage}})
    else:
        frame = frames[0]
        assert isinstance(frame, dict)
        frame[key] = raw_usage
    with pytest.raises(ProviderError) as captured:
        await invoke(provider, streaming=streaming, body=body, frames=frames if streaming else None)
    assert captured.value.retryable is False
    assert "PRIVATE-SENTINEL" not in str(captured.value)
