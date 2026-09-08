"""OpenAI-compatible chat-completions adapter using canonical runtime types."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
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
from agent_runtime.models_clients.base import ModelResult
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
            response = await self._client.post(
                "/chat/completions",
                json=payload,
                headers={"Authorization": self._authorization},
            )
            response.raise_for_status()
            body = self._mapping(cast(object, response.json()), "response")
            choices = self._sequence(body.get("choices"), "choices")
            choice = self._mapping(choices[0], "choice")
            provider_message = self._mapping(choice.get("message"), "message")
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("OpenAI-compatible model request failed") from exc

        content_parts: list[ContentPart] = []
        content = provider_message.get("content")
        if isinstance(content, str) and content:
            content_parts.append(TextContent(text=content))
        tool_calls: list[ToolCall] = []
        raw_tool_calls = provider_message.get("tool_calls", [])
        if isinstance(raw_tool_calls, Sequence) and not isinstance(raw_tool_calls, (str, bytes)):
            for raw_value in cast(Sequence[object], raw_tool_calls):
                try:
                    raw = self._mapping(raw_value, "tool call")
                    function = self._mapping(raw.get("function"), "tool function")
                    arguments = json.loads(str(function.get("arguments", "{}")))
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be an object")
                    argument_mapping = cast(dict[str, Any], arguments)
                    call = ToolCall(
                        id=str(raw["id"]),
                        name=str(function["name"]),
                        arguments=argument_mapping,
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ProviderError("Model returned an invalid tool call") from exc
                tool_calls.append(call)
                content_parts.append(ToolCallContent(tool_call=call))
        if not content_parts:
            raise ProviderError("Model returned an empty assistant message")

        usage = None
        raw_usage = body.get("usage")
        if isinstance(raw_usage, Mapping):
            usage_mapping = cast(Mapping[str, object], raw_usage)
            usage = Usage(
                input_tokens=int(str(usage_mapping.get("prompt_tokens", 0))),
                output_tokens=int(str(usage_mapping.get("completion_tokens", 0))),
            )
        return ModelResult(
            message=Message(role="assistant", content=content_parts),
            tool_calls=tool_calls,
            usage=usage,
        )

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
