"""OpenAI-compatible chat-completions adapter using canonical runtime types."""

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
    Usage,
)
from agent_runtime.models_clients._canonical import (
    assembled_result,
    candidate_index,
    token_usage,
    tool_index,
    tool_string,
)
from agent_runtime.models_clients._http import request_failure, response_json, sse_data
from agent_runtime.models_clients.base import (
    ModelCompleted,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
)
from agent_runtime.tools.models import ToolDefinition


class OpenAICompatibleModelClient:
    """Translate canonical messages to an OpenAI-compatible HTTP endpoint."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model or model == "replace-me":
            raise ValueError("A real model name is required")
        if not api_key:
            raise ValueError("A model API key is required")
        self.model = model
        self._authorization = f"Bearer {api_key}"
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
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
        """Generate and translate one non-streaming chat completion."""
        del context
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [self._message_payload(message) for message in messages],
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in tools
            ]
        try:
            raw_body = await response_json(
                self._client,
                "/chat/completions",
                payload=payload,
                headers={"Authorization": self._authorization},
            )
            body = self._mapping(raw_body, "response")
            choices = self._sequence(body.get("choices"), "choices")
            if len(choices) != 1:
                raise ProviderError("Model returned multiple choices", retryable=False)
            choice = self._mapping(choices[0], "choice")
            candidate_index(choice)
            if choice.get("finish_reason") not in (None, "stop", "tool_calls"):
                raise ProviderError("Model response did not finish successfully", retryable=False)
            provider_message = self._mapping(choice.get("message"), "message")
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise request_failure("OpenAI-compatible model request failed", exc) from exc

        text_chunks: list[str] = []
        content = provider_message.get("content")
        if isinstance(content, str) and content:
            text_chunks.append(content)
        tool_calls: list[ToolCall] = []
        raw_tool_calls = provider_message.get("tool_calls", [])
        if raw_tool_calls is not None and (
            not isinstance(raw_tool_calls, Sequence) or isinstance(raw_tool_calls, (str, bytes))
        ):
            raise ProviderError("Model returned invalid tool calls", retryable=False)
        if isinstance(raw_tool_calls, Sequence) and not isinstance(raw_tool_calls, (str, bytes)):
            for raw_value in cast(Sequence[object], raw_tool_calls):
                try:
                    raw = self._mapping(raw_value, "tool call")
                    function = self._mapping(raw.get("function"), "tool function")
                    arguments = json.loads(tool_string(function.get("arguments", "{}")))
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be an object")
                    argument_mapping = cast(dict[str, Any], arguments)
                    call = ToolCall(
                        id=tool_string(raw["id"]),
                        name=tool_string(function["name"]),
                        arguments=argument_mapping,
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ProviderError(
                        "Model returned an invalid tool call", retryable=False
                    ) from exc
                tool_calls.append(call)
        if not text_chunks and not tool_calls:
            raise ProviderError("Model returned an empty assistant message", retryable=False)

        usage = token_usage(body.get("usage"), "prompt_tokens", "completion_tokens")
        return assembled_result(text_chunks, tool_calls, usage)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream chat-completion deltas and finish with a canonical model result."""
        del context
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [self._message_payload(message) for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in tools
            ]

        text_chunks: list[str] = []
        tool_chunks: dict[int, dict[str, str]] = {}
        usage: Usage | None = None
        completed = False
        try:
            async with self._client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                headers={"Authorization": self._authorization},
            ) as response:
                response.raise_for_status()
                async for data in sse_data(response):
                    if not data:
                        continue
                    if data == "[DONE]":
                        completed = True
                        break
                    body = self._mapping(cast(object, json.loads(data)), "stream event")
                    if "error" in body:
                        raise ProviderError("OpenAI-compatible model stream returned an error")
                    usage = (
                        token_usage(body.get("usage"), "prompt_tokens", "completion_tokens")
                        or usage
                    )
                    choices = body.get("choices")
                    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
                        raise ProviderError(
                            "Model returned invalid stream choices", retryable=False
                        )
                    choices = cast(Sequence[object], choices)
                    if len(choices) > 1:
                        raise ProviderError(
                            "Model returned multiple stream choices", retryable=False
                        )
                    for raw_choice in choices:
                        choice = self._mapping(raw_choice, "stream choice")
                        candidate_index(choice)
                        if choice.get("finish_reason") not in (None, "stop", "tool_calls"):
                            raise ProviderError(
                                "Model stream did not finish successfully", retryable=False
                            )
                        delta = self._mapping(choice.get("delta"), "stream delta")
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            text_chunks.append(content)
                            yield ModelTextDelta(delta=content)
                        raw_calls = delta.get("tool_calls", [])
                        if raw_calls is not None and (
                            not isinstance(raw_calls, Sequence)
                            or isinstance(raw_calls, (str, bytes))
                        ):
                            raise ProviderError(
                                "Model returned invalid tool calls", retryable=False
                            )
                        if isinstance(raw_calls, Sequence) and not isinstance(
                            raw_calls, (str, bytes)
                        ):
                            for raw_call in cast(Sequence[object], raw_calls):
                                call = self._mapping(raw_call, "stream tool call")
                                index = tool_index(call.get("index", 0))
                                chunk = tool_chunks.setdefault(
                                    index, {"id": "", "name": "", "arguments": ""}
                                )
                                if call.get("id") is not None:
                                    chunk["id"] += tool_string(call["id"])
                                function = call.get("function")
                                if function is not None and not isinstance(function, Mapping):
                                    raise ProviderError(
                                        "Model returned an invalid tool function", retryable=False
                                    )
                                if isinstance(function, Mapping):
                                    function_mapping = cast(Mapping[str, object], function)
                                    name = function_mapping.get("name")
                                    arguments = function_mapping.get("arguments")
                                    if name is not None:
                                        chunk["name"] += tool_string(name)
                                    if arguments is not None:
                                        chunk["arguments"] += tool_string(arguments)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise request_failure("OpenAI-compatible model stream failed", exc) from exc

        if not completed:
            raise ProviderError("OpenAI-compatible model stream ended before completion")

        tool_calls: list[ToolCall] = []
        try:
            for chunk in (tool_chunks[index] for index in sorted(tool_chunks)):
                arguments = json.loads(chunk["arguments"] or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                call = ToolCall(
                    id=chunk["id"],
                    name=chunk["name"],
                    arguments=cast(dict[str, Any], arguments),
                )
                tool_calls.append(call)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "Model returned an invalid streamed tool call", retryable=False
            ) from exc
        if not text_chunks and not tool_calls:
            raise ProviderError(
                "Model returned an empty streamed assistant message", retryable=False
            )
        yield ModelCompleted(result=assembled_result(text_chunks, tool_calls, usage))

    @staticmethod
    def _message_payload(message: Message) -> dict[str, object]:
        text_parts = [part.text for part in message.content if isinstance(part, TextContent)]
        if message.role == "tool":
            result_part = next(
                (part for part in message.content if part.type == "tool_result"), None
            )
            if result_part is not None and result_part.type == "tool_result":
                return {
                    "role": "tool",
                    "tool_call_id": result_part.tool_call_id,
                    "content": json.dumps(result_part.result, ensure_ascii=True),
                }
        payload: dict[str, object] = {
            "role": message.role,
            "content": "\n".join(text_parts) or None,
        }
        calls = [part.tool_call for part in message.content if part.type == "tool_call"]
        if calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=True),
                    },
                }
                for call in calls
            ]
        return payload

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError(f"Provider {label} must be an object")
        return cast(Mapping[str, object], value)

    @staticmethod
    def _sequence(value: object, label: str) -> Sequence[object]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise TypeError(f"Provider {label} must be an array")
        if not value:
            raise ValueError(f"Provider {label} must not be empty")
        return cast(Sequence[object], value)
