"""Anthropic Messages API adapter using canonical runtime types."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast

import httpx

from agent_runtime.errors import ProviderError
from agent_runtime.models import (
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
    Usage,
)
from agent_runtime.models_clients._canonical import assembled_result, token_count, token_usage
from agent_runtime.models_clients._http import request_failure, response_json, sse_data
from agent_runtime.models_clients.base import (
    ModelCompleted,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
)
from agent_runtime.tools.models import ToolDefinition


class AnthropicModelClient:
    """Translate canonical requests to Anthropic's public Messages API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        max_tokens: int = 1_024,
        base_url: str = "https://api.anthropic.com",
        api_version: str = "2023-06-01",
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model or model == "replace-me":
            raise ValueError("A real model name is required")
        if not api_key:
            raise ValueError("An Anthropic API key is required")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.model = model
        self._max_tokens = max_tokens
        self._headers = {"x-api-key": api_key, "anthropic-version": api_version}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def close(self) -> None:
        """Close the internally owned HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        """Generate and translate one Anthropic message."""
        del context
        try:
            body = await response_json(
                self._client,
                "/v1/messages",
                payload=self._payload(messages, tools=tools, stream=False),
                headers=self._headers,
            )
            return self._result(self._mapping(body, "response"))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise request_failure("Anthropic model request failed", exc) from exc

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream Anthropic content blocks and return their canonical aggregate."""
        del context
        text_chunks: list[str] = []
        tool_chunks: dict[int, dict[str, str]] = {}
        input_tokens: int | None = None
        output_tokens: int | None = None
        completed = False
        try:
            async with self._client.stream(
                "POST",
                "/v1/messages",
                json=self._payload(messages, tools=tools, stream=True),
                headers=self._headers,
            ) as response:
                response.raise_for_status()
                async for data in sse_data(response):
                    if not data:
                        continue
                    event = self._mapping(cast(object, json.loads(data)), "stream event")
                    event_type = event.get("type")
                    if event_type == "error":
                        raise ProviderError("Anthropic model stream returned an error")
                    if event_type == "message_stop":
                        completed = True
                        break
                    if event_type == "message_start":
                        message = self._mapping(event.get("message"), "stream message")
                        raw_usage = message.get("usage")
                        usage = self._mapping(
                            {} if raw_usage is None else raw_usage, "stream usage"
                        )
                        input_tokens = token_count(usage.get("input_tokens"))
                        output_tokens = token_count(usage.get("output_tokens"))
                    elif event_type == "content_block_start":
                        index = int(str(event.get("index", 0)))
                        block = self._mapping(event.get("content_block"), "content block")
                        if block.get("type") == "tool_use":
                            initial_input = block.get("input", {})
                            tool_chunks[index] = {
                                "id": str(block["id"]),
                                "name": str(block["name"]),
                                "arguments": (
                                    json.dumps(initial_input)
                                    if initial_input not in ({}, None)
                                    else ""
                                ),
                            }
                    elif event_type == "content_block_delta":
                        index = int(str(event.get("index", 0)))
                        delta = self._mapping(event.get("delta"), "content delta")
                        if delta.get("type") == "text_delta":
                            text = delta.get("text")
                            if isinstance(text, str) and text:
                                text_chunks.append(text)
                                yield ModelTextDelta(delta=text)
                        elif delta.get("type") == "input_json_delta":
                            partial = delta.get("partial_json")
                            if isinstance(partial, str):
                                tool_chunks[index]["arguments"] += partial
                    elif event_type == "message_delta":
                        delta = self._mapping(event.get("delta", {}), "message delta")
                        if delta.get("stop_reason") not in (
                            None,
                            "end_turn",
                            "stop_sequence",
                            "tool_use",
                        ):
                            raise ProviderError(
                                "Anthropic model stream did not finish successfully",
                                retryable=False,
                            )
                        raw_usage = event.get("usage")
                        usage = self._mapping(
                            {} if raw_usage is None else raw_usage, "stream usage"
                        )
                        if "input_tokens" in usage:
                            input_tokens = token_count(usage["input_tokens"])
                        if "output_tokens" in usage:
                            output_tokens = token_count(usage["output_tokens"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise request_failure("Anthropic model stream failed", exc) from exc

        if not completed:
            raise ProviderError("Anthropic model stream ended before completion")

        result = self._assembled_result(
            text_chunks,
            tool_chunks,
            token_usage({"input": input_tokens, "output": output_tokens}, "input", "output"),
        )
        yield ModelCompleted(result=result)

    def _payload(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition],
        stream: bool,
    ) -> dict[str, object]:
        system_parts: list[str] = []
        provider_messages: list[dict[str, object]] = []
        for message in messages:
            if message.role == "system":
                system_parts.extend(
                    part.text for part in message.content if isinstance(part, TextContent)
                )
                continue
            content: list[dict[str, object]] = []
            for part in message.content:
                if isinstance(part, TextContent):
                    content.append({"type": "text", "text": part.text})
                elif isinstance(part, ToolCallContent):
                    content.append(
                        {
                            "type": "tool_use",
                            "id": part.tool_call.id,
                            "name": part.tool_call.name,
                            "input": part.tool_call.arguments,
                        }
                    )
                else:
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": part.tool_call_id,
                            "content": json.dumps(part.result, ensure_ascii=True),
                        }
                    )
            provider_messages.append(
                {"role": "user" if message.role == "tool" else message.role, "content": content}
            )
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self._max_tokens,
            "messages": provider_messages,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n".join(system_parts)
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": dict(tool.input_schema),
                }
                for tool in tools
            ]
        return payload

    def _result(self, body: Mapping[str, object]) -> ModelResult:
        if body.get("stop_reason") not in (None, "end_turn", "stop_sequence", "tool_use"):
            raise ProviderError("Anthropic response did not finish successfully", retryable=False)
        raw_content = self._sequence(body.get("content"), "content")
        text_chunks: list[str] = []
        tool_chunks: dict[int, dict[str, str]] = {}
        for index, raw_block in enumerate(raw_content):
            block = self._mapping(raw_block, "content block")
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    text_chunks.append(text)
            elif block.get("type") == "tool_use":
                tool_chunks[index] = {
                    "id": str(block["id"]),
                    "name": str(block["name"]),
                    "arguments": json.dumps(block.get("input", {})),
                }
        usage = token_usage(body.get("usage"), "input_tokens", "output_tokens")
        return self._assembled_result(text_chunks, tool_chunks, usage)

    @staticmethod
    def _assembled_result(
        text_chunks: list[str],
        tool_chunks: dict[int, dict[str, str]],
        usage: Usage | None,
    ) -> ModelResult:
        tool_calls: list[ToolCall] = []
        try:
            for chunk in (tool_chunks[index] for index in sorted(tool_chunks)):
                arguments = json.loads(chunk["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Tool input must be an object")
                call = ToolCall(
                    id=chunk["id"],
                    name=chunk["name"],
                    arguments=cast(dict[str, Any], arguments),
                )
                tool_calls.append(call)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("Anthropic returned an invalid tool call", retryable=False) from exc
        if not text_chunks and not tool_calls:
            raise ProviderError("Anthropic returned an empty assistant message", retryable=False)
        return assembled_result(text_chunks, tool_calls, usage)

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError(f"Anthropic {label} must be an object")
        return cast(Mapping[str, object], value)

    @staticmethod
    def _sequence(value: object, label: str) -> Sequence[object]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise TypeError(f"Anthropic {label} must be an array")
        return cast(Sequence[object], value)
