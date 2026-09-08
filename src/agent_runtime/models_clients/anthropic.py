"""Anthropic Messages API adapter using canonical runtime types."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast

import httpx

from agent_runtime.errors import ProviderError
from agent_runtime.models import (
    ContentPart,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
    Usage,
)
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
            response = await self._client.post(
                "/v1/messages",
                json=self._payload(messages, tools=tools, stream=False),
                headers=self._headers,
            )
            response.raise_for_status()
            return self._result(self._mapping(cast(object, response.json()), "response"))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError("Anthropic model request failed") from exc

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
        input_tokens = 0
        output_tokens = 0
        try:
            async with self._client.stream(
                "POST",
                "/v1/messages",
                json=self._payload(messages, tools=tools, stream=True),
                headers=self._headers,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data:
                        continue
                    event = self._mapping(cast(object, json.loads(data)), "stream event")
                    event_type = event.get("type")
                    if event_type == "message_start":
                        message = self._mapping(event.get("message"), "stream message")
                        usage = self._mapping(message.get("usage", {}), "stream usage")
                        input_tokens = int(str(usage.get("input_tokens", 0)))
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
                        usage = self._mapping(event.get("usage", {}), "stream usage")
                        output_tokens = int(str(usage.get("output_tokens", output_tokens)))
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("Anthropic model stream failed") from exc

        result = self._assembled_result(
            text_chunks,
            tool_chunks,
            Usage(input_tokens=input_tokens, output_tokens=output_tokens),
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
        raw_usage = self._mapping(body.get("usage", {}), "usage")
        usage = Usage(
            input_tokens=int(str(raw_usage.get("input_tokens", 0))),
            output_tokens=int(str(raw_usage.get("output_tokens", 0))),
        )
        return self._assembled_result(text_chunks, tool_chunks, usage)

    @staticmethod
    def _assembled_result(
        text_chunks: list[str],
        tool_chunks: dict[int, dict[str, str]],
        usage: Usage,
    ) -> ModelResult:
        content_parts: list[ContentPart] = []
        if text_chunks:
            content_parts.append(TextContent(text="".join(text_chunks)))
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
                content_parts.append(ToolCallContent(tool_call=call))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("Anthropic returned an invalid tool call") from exc
        if not content_parts:
            raise ProviderError("Anthropic returned an empty assistant message")
        return ModelResult(
            message=Message(role="assistant", content=content_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

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
