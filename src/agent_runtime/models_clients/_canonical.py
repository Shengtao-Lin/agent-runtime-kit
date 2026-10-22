"""Private validation shared by the HTTP adapters, not a vendor wire schema."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from agent_runtime.errors import ProviderError
from agent_runtime.models import ContentPart, Message, TextContent, ToolCall, ToolCallContent, Usage
from agent_runtime.models_clients.base import ModelResult


def token_count(value: object) -> int | None:
    """Preserve unknown counts; reject lossy coercion and impossible supplied values."""
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ProviderError("Model returned invalid usage", retryable=False)
    return value


def token_usage(raw: object, input_key: str, output_key: str) -> Usage | None:
    """Only expose canonical usage when both non-negative counts are known."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ProviderError("Model returned invalid usage", retryable=False)
    counts = cast(Mapping[str, object], raw)
    input_tokens = token_count(counts.get(input_key))
    output_tokens = token_count(counts.get(output_key))
    if input_tokens is None or output_tokens is None:
        return None
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def candidate_index(candidate: Mapping[str, object]) -> None:
    """The adapters request one result; do not combine different indexed candidates."""
    index = candidate.get("index", 0)
    if type(index) is not int or index != 0:
        raise ProviderError("Model returned an unsupported candidate index", retryable=False)


def assembled_result(
    text_chunks: list[str], tool_calls: list[ToolCall], usage: Usage | None
) -> ModelResult:
    """Keep canonical validation failures safe even without a surrounding runtime."""
    try:
        parts: list[ContentPart] = []
        if text_chunks:
            parts.append(TextContent(text="".join(text_chunks)))
        parts.extend(ToolCallContent(tool_call=call) for call in tool_calls)
        return ModelResult(
            message=Message(role="assistant", content=parts), tool_calls=tool_calls, usage=usage
        )
    except ValueError as exc:
        raise ProviderError("Model returned an invalid canonical message", retryable=False) from exc
