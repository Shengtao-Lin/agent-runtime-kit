"""Opt-in LangChain/LangGraph message translation using public message types.

Import this module only with the ``adapters`` extra installed. Core dictionary
mapping and core package imports remain independent of framework dependencies.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    BaseMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent_runtime.models import (
    ContentPart,
    InvocationInput,
    InvocationOutput,
    Message,
    TextContent,
    ToolCall,
    ToolCallContent,
    ToolResult,
    ToolResultContent,
    Usage,
)


def _identity(value: str | None) -> UUID:
    if value is None:
        return uuid4()
    try:
        return UUID(value)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"agent-runtime-kit/framework-message/{value}")


def _text(content: str | list[str | dict[Any, Any]]) -> list[ContentPart]:
    blocks = [content] if isinstance(content, str) else content
    parts: list[ContentPart] = []
    for block in blocks:
        if isinstance(block, str):
            text = block
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            text = cast(str, block["text"])
        else:
            raise ValueError("Unsupported framework content block; provide an explicit mapper")
        if text:
            parts.append(TextContent(text=text))
    return parts


def to_framework_message(message: Message) -> BaseMessage:
    """Translate canonical text/tool messages without inventing a second history store."""
    identity = str(message.id)
    if message.role == "tool":
        if len(message.content) != 1 or not isinstance(message.content[0], ToolResultContent):
            raise ValueError("A framework ToolMessage requires one tool result")
        part = message.content[0]
        return ToolMessage(
            id=identity,
            content=json.dumps(part.result, ensure_ascii=False),
            tool_call_id=part.tool_call_id,
        )
    text: list[str | dict[Any, Any]] = [
        {"type": "text", "text": part.text}
        for part in message.content
        if isinstance(part, TextContent)
    ]
    if message.role == "assistant":
        return AIMessage(
            id=identity,
            content=text,
            tool_calls=[
                {
                    "id": part.tool_call.id,
                    "name": part.tool_call.name,
                    "args": part.tool_call.arguments,
                    "type": "tool_call",
                }
                for part in message.content
                if isinstance(part, ToolCallContent)
            ],
        )
    if message.role == "system":
        return SystemMessage(id=identity, content=text)
    return HumanMessage(id=identity, content=text)


def from_framework_message(message: BaseMessage) -> Message:
    """Map supported native messages, rejecting unsupported blocks rather than dropping them."""
    if isinstance(message, BaseMessageChunk):
        raise ValueError("Partial framework chunks require an explicit streaming adapter")
    identity = _identity(message.id)
    if isinstance(message, ToolMessage):
        parts = _text(message.content)
        text = "".join(part.text for part in parts if isinstance(part, TextContent))
        try:
            result: object = json.loads(text)
        except json.JSONDecodeError:
            result = text
        return Message(
            id=identity,
            role="tool",
            content=[
                ToolResultContent(
                    tool_call_id=message.tool_call_id,
                    result=result,
                )
            ],
        )
    parts = _text(message.content)
    if isinstance(message, AIMessage):
        if message.invalid_tool_calls:
            raise ValueError("Framework output contains invalid tool calls")
        parts.extend(
            ToolCallContent(
                tool_call=ToolCall(
                    id=call["id"] or "",
                    name=call["name"],
                    arguments=call["args"],
                )
            )
            for call in message.tool_calls
        )
        return Message(id=identity, role="assistant", content=parts)
    if isinstance(message, HumanMessage):
        return Message(id=identity, role="user", content=parts)
    if isinstance(message, SystemMessage):
        return Message(id=identity, role="system", content=parts)
    raise ValueError("Unsupported framework message type; provide an explicit mapper")


def framework_input(request: InvocationInput) -> dict[str, object]:
    """Supply native messages plus canonical context to a runnable or compiled graph."""
    return {
        "messages": [to_framework_message(message) for message in request.messages],
        "context": request.context.model_dump(mode="json"),
        "metadata": request.metadata,
    }


def framework_output(value: object, request: InvocationInput) -> InvocationOutput:
    """Normalize a native message/list/state, retaining only newly generated messages."""
    from agent_runtime.adapters.mapping import messages_output

    envelope: dict[str, object]
    if isinstance(value, BaseMessage):
        envelope = {"messages": [value]}
    elif isinstance(value, Mapping):
        envelope = dict(cast(Mapping[str, object], value))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        envelope = {"messages": value}
    else:
        raise TypeError("Framework output must be a supported message, message list, or state")
    raw = envelope.get("messages")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise TypeError("Framework output must contain a message sequence")
    originals = list(cast(Sequence[object], raw))
    converted = [
        from_framework_message(item)
        if isinstance(item, BaseMessage)
        else Message.model_validate(item)
        for item in originals
    ]
    prefix = len(request.messages) if converted[: len(request.messages)] == request.messages else 0
    names = {
        part.tool_call.id: part.tool_call.name
        for message in [*request.messages, *converted]
        for part in message.content
        if isinstance(part, ToolCallContent)
    }
    results: list[ToolResult] = []
    usage: Usage | None = None
    for original, canonical in zip(originals[prefix:], converted[prefix:], strict=True):
        if isinstance(original, AIMessage) and original.usage_metadata is not None:
            usage = Usage(
                input_tokens=original.usage_metadata["input_tokens"],
                output_tokens=original.usage_metadata["output_tokens"],
            )
        if isinstance(original, ToolMessage):
            name = original.name or names.get(original.tool_call_id)
            if not name:
                raise ValueError("Tool output needs a name or a matching tool call")
            part = canonical.content[0]
            assert isinstance(part, ToolResultContent)
            results.append(
                ToolResult(
                    id=uuid5(canonical.id, "tool-result"),
                    tool_call_id=original.tool_call_id,
                    tool_name=name,
                    status="failed" if original.status == "error" else "succeeded",
                    output=part.result,
                    error_code="tool_execution_failed" if original.status == "error" else None,
                )
            )
    return messages_output(
        {
            "messages": converted,
            "metadata": envelope.get("metadata", {}),
            "tool_results": envelope.get("tool_results", results),
            "usage": envelope.get("usage", usage),
        },
        request,
    )
