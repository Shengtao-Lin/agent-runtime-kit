"""Google Gemini generateContent adapter using canonical runtime types."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast
from urllib.parse import quote
from uuid import uuid4

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


class GeminiModelClient:
    """Translate canonical requests to Google's Gemini generateContent API."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model or model == "replace-me":
            raise ValueError("A real model name is required")
        if not api_key:
            raise ValueError("A Gemini API key is required")
        self.model = model
        self._api_key = api_key
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
        """Generate and translate one Gemini response."""
        del context
        try:
            response = await self._client.post(
                self._path(stream=False),
                headers={"x-goog-api-key": self._api_key},
                json=self._payload(messages, tools=tools),
            )
            response.raise_for_status()
            body = self._mapping(cast(object, response.json()), "response")
            return self._result(body)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            if isinstance(exc, ProviderError):
                raise
            raise ProviderError("Gemini model request failed") from exc

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream Gemini SSE chunks and return their canonical aggregate."""
        del context
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        usage: Usage | None = None
        try:
            async with self._client.stream(
                "POST",
                self._path(stream=True),
                params={"alt": "sse"},
                headers={"x-goog-api-key": self._api_key},
                json=self._payload(messages, tools=tools),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if not data:
                        continue
                    body = self._mapping(cast(object, json.loads(data)), "stream event")
                    parts = self._parts(body)
                    for part in parts:
                        text = part.get("text")
                        if isinstance(text, str) and text:
                            text_chunks.append(text)
                            yield ModelTextDelta(delta=text)
                        function = part.get("functionCall")
                        if isinstance(function, Mapping):
                            tool_calls.append(self._tool_call(cast(Mapping[str, object], function)))
                    usage = self._usage(body) or usage
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("Gemini model stream failed") from exc
        yield ModelCompleted(result=self._assembled_result(text_chunks, tool_calls, usage))

    def _path(self, *, stream: bool) -> str:
        method = "streamGenerateContent" if stream else "generateContent"
        return f"/v1beta/models/{quote(self.model, safe='')}:{method}"

    @staticmethod
    def _payload(
        messages: Sequence[Message], *, tools: Sequence[ToolDefinition]
    ) -> dict[str, object]:
        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, object]] = []
        call_names = {
            part.tool_call.id: part.tool_call.name
            for message in messages
            for part in message.content
            if isinstance(part, ToolCallContent)
        }
        for message in messages:
            if message.role == "system":
                system_parts.extend(
                    {"text": part.text} for part in message.content if isinstance(part, TextContent)
                )
                continue
            parts: list[dict[str, object]] = []
            for part in message.content:
                if isinstance(part, TextContent):
                    parts.append({"text": part.text})
                elif isinstance(part, ToolCallContent):
                    parts.append(
                        {
                            "functionCall": {
                                "name": part.tool_call.name,
                                "args": part.tool_call.arguments,
                            }
                        }
                    )
                else:
                    name = call_names.get(part.tool_call_id)
                    if name is None:
                        raise ProviderError("Gemini tool result has no matching tool call")
                    raw_result: object = part.result
                    response: dict[str, Any] = (
                        cast(dict[str, Any], raw_result)
                        if isinstance(raw_result, dict)
                        else {"result": raw_result}
                    )
                    parts.append({"functionResponse": {"name": name, "response": response}})
            contents.append(
                {"role": "model" if message.role == "assistant" else "user", "parts": parts}
            )
        payload: dict[str, object] = {"contents": contents}
        if system_parts:
            payload["systemInstruction"] = {"parts": system_parts}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": dict(tool.input_schema),
                        }
                        for tool in tools
                    ]
                }
            ]
        return payload

    def _result(self, body: Mapping[str, object]) -> ModelResult:
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in self._parts(body):
            text = part.get("text")
            if isinstance(text, str) and text:
                text_chunks.append(text)
            function = part.get("functionCall")
            if isinstance(function, Mapping):
                tool_calls.append(self._tool_call(cast(Mapping[str, object], function)))
        return self._assembled_result(text_chunks, tool_calls, self._usage(body))

    @classmethod
    def _parts(cls, body: Mapping[str, object]) -> list[Mapping[str, object]]:
        candidates = cls._sequence(body.get("candidates"), "candidates")
        candidate = cls._mapping(candidates[0], "candidate")
        content = cls._mapping(candidate.get("content"), "content")
        raw_parts = cls._sequence(content.get("parts"), "parts")
        return [cls._mapping(part, "part") for part in raw_parts]

    @staticmethod
    def _tool_call(function: Mapping[str, object]) -> ToolCall:
        arguments = function.get("args", {})
        if not isinstance(arguments, dict):
            raise ProviderError("Gemini returned invalid tool arguments")
        return ToolCall(
            id=f"gemini-{uuid4()}",
            name=str(function["name"]),
            arguments=cast(dict[str, Any], arguments),
        )

    @classmethod
    def _usage(cls, body: Mapping[str, object]) -> Usage | None:
        raw = body.get("usageMetadata")
        if not isinstance(raw, Mapping):
            return None
        usage = cast(Mapping[str, object], raw)
        return Usage(
            input_tokens=int(str(usage.get("promptTokenCount", 0))),
            output_tokens=int(str(usage.get("candidatesTokenCount", 0))),
        )

    @staticmethod
    def _assembled_result(
        text_chunks: list[str], tool_calls: list[ToolCall], usage: Usage | None
    ) -> ModelResult:
        parts: list[ContentPart] = []
        if text_chunks:
            parts.append(TextContent(text="".join(text_chunks)))
        parts.extend(ToolCallContent(tool_call=call) for call in tool_calls)
        if not parts:
            raise ProviderError("Gemini returned an empty assistant message")
        return ModelResult(
            message=Message(role="assistant", content=parts),
            tool_calls=tool_calls,
            usage=usage,
        )

    @staticmethod
    def _mapping(value: object, label: str) -> Mapping[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError(f"Gemini {label} must be an object")
        return cast(Mapping[str, object], value)

    @staticmethod
    def _sequence(value: object, label: str) -> Sequence[object]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
            raise ValueError(f"Gemini {label} must be a non-empty array")
        return cast(Sequence[object], value)
