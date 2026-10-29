"""Custom model results must describe exactly the tools the native loop executes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.errors import ProviderError
from agent_runtime.models import (
    InvocationInput,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
    Usage,
)
from agent_runtime.models_clients import ModelCompleted, ModelResult, ModelStreamEvent
from agent_runtime.tools import ToolDefinition, ToolRegistry

Mode = Literal["invoke", "stream", "stream_fallback"]
INVALID_SHAPES = (
    "missing-list",
    "missing-parts",
    "reordered",
    "different-id",
    "different-name",
    "different-arguments",
    "duplicate-id",
    "wrong-role",
    "nonfinite",
    "python-object",
)


class NoArguments(BaseModel):
    pass


def valid_result() -> ModelResult:
    first = ToolCall(id="first", name="lookup", arguments={"flag": True})
    second = ToolCall(id="second", name="lookup", arguments={})
    return ModelResult(
        message=Message(
            role="assistant",
            content=[
                ToolCallContent(tool_call=first),
                TextContent(text="Two independent lookups"),
                ToolCallContent(tool_call=second),
            ],
        ),
        tool_calls=[first, second],
        usage=Usage(input_tokens=0, output_tokens=0),
    )


def invalid_result(shape: str) -> ModelResult:
    result = valid_result()
    if shape == "missing-list":
        result.tool_calls.clear()
    elif shape == "missing-parts":
        result.message.content = [TextContent(text="PRIVATE-SENTINEL")]
    elif shape == "reordered":
        result.tool_calls.reverse()
    elif shape == "different-id":
        result.tool_calls[1] = result.tool_calls[1].model_copy(update={"id": "different"})
    elif shape == "different-name":
        result.tool_calls[1] = result.tool_calls[1].model_copy(update={"name": "different"})
    elif shape == "different-arguments":
        result.tool_calls[0] = result.tool_calls[0].model_copy(update={"arguments": {"flag": 1}})
    elif shape == "duplicate-id":
        result.tool_calls[1].id = result.tool_calls[0].id
    elif shape == "wrong-role":
        result.message = Message(role="user", content=[TextContent(text="PRIVATE-SENTINEL")])
        result.tool_calls = []
    elif shape == "nonfinite":
        result.tool_calls[1].arguments["PRIVATE-SENTINEL"] = float("nan")
    elif shape == "python-object":
        result.tool_calls[1].arguments["PRIVATE-SENTINEL"] = object()
    else:
        raise AssertionError(shape)
    return result


class CustomModel:
    def __init__(self, result: ModelResult, *, construct_inside: bool = False) -> None:
        self.result = result
        self.construct_inside = construct_inside
        self.calls = 0
        self.closed = False

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        del messages, tools, context
        self.calls += 1
        if self.calls > 1:
            return ModelResult(
                message=Message(role="assistant", content=[TextContent(text="done")]),
                usage=Usage(input_tokens=0, output_tokens=0),
            )
        if self.construct_inside:
            return ModelResult(message=self.result.message, tool_calls=self.result.tool_calls)
        return self.result


class StreamingCustomModel(CustomModel):
    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        try:
            result = await self.generate(messages, tools=tools, context=context)
            # Deliberately bypass event construction to test the consumption boundary too.
            yield ModelCompleted.model_construct(result=result)
        finally:
            self.closed = True


def invocation() -> InvocationInput:
    return InvocationInput(
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        messages=[Message(role="user", content=[TextContent(text="lookup")])],
    )


@pytest.mark.parametrize("shape", INVALID_SHAPES)
def test_model_result_construction_rejects_inconsistent_or_invalid_calls(shape: str) -> None:
    result = invalid_result(shape)
    with pytest.raises(ValidationError):
        ModelResult(message=result.message, tool_calls=result.tool_calls)


@pytest.mark.parametrize("mode", ["invoke", "stream", "stream_fallback"])
@pytest.mark.parametrize("shape", INVALID_SHAPES)
async def test_native_rechecks_custom_results_before_any_tool(shape: str, mode: Mode) -> None:
    result = invalid_result(shape)
    model = StreamingCustomModel(result) if mode == "stream" else CustomModel(result)
    executions: list[str] = []
    tools = ToolRegistry()
    tools.register(
        name="lookup",
        description="Record a synthetic side effect.",
        input_model=NoArguments,
        handler=lambda: executions.append("lookup"),
    )
    invoker = NativeAgentInvoker(agent_id="custom", version="1", model_client=model, tools=tools)
    seen: list[str] = []
    with pytest.raises(ProviderError) as caught:
        if mode == "invoke":
            await invoker.invoke(invocation())
        else:
            async for event in invoker.stream(invocation()):
                seen.append(event.type)
    assert caught.value.retryable is False
    assert "PRIVATE-SENTINEL" not in caught.value.public_message
    assert executions == []
    assert "completed" not in seen and "tool_result" not in seen
    assert model.calls == 1
    if mode == "stream":
        assert model.closed


@pytest.mark.parametrize("mode", ["invoke", "stream", "stream_fallback"])
async def test_validation_raised_inside_custom_provider_is_safe_and_nonretryable(
    mode: Mode,
) -> None:
    result = invalid_result("missing-list")
    model = (
        StreamingCustomModel(result, construct_inside=True)
        if mode == "stream"
        else CustomModel(result, construct_inside=True)
    )
    invoker = NativeAgentInvoker(agent_id="custom", version="1", model_client=model)
    with pytest.raises(ProviderError) as caught:
        if mode == "invoke":
            await invoker.invoke(invocation())
        else:
            _ = [event async for event in invoker.stream(invocation())]
    assert caught.value.retryable is False


@pytest.mark.parametrize("bypass", ["copy", "construct"])
async def test_unvalidated_pydantic_construction_does_not_bypass_native_checks(bypass: str) -> None:
    valid = valid_result()
    result = (
        valid.model_copy(update={"tool_calls": []})
        if bypass == "copy"
        else ModelResult.model_construct(message=valid.message, tool_calls=[])
    )
    invoker = NativeAgentInvoker(agent_id="custom", version="1", model_client=CustomModel(result))
    with pytest.raises(ProviderError) as caught:
        await invoker.invoke(invocation())
    assert caught.value.retryable is False


@pytest.mark.parametrize("mode", ["invoke", "stream", "stream_fallback"])
async def test_valid_result_is_snapshotted_before_tool_execution(mode: Mode) -> None:
    result = valid_result()
    model = StreamingCustomModel(result) if mode == "stream" else CustomModel(result)
    executions: list[str] = []

    def lookup() -> None:
        executions.append("lookup")
        # An extension retains the original models; it must not change later dispatch/history.
        result.tool_calls[1].name = "PRIVATE-SENTINEL"

    tools = ToolRegistry()
    tools.register(
        name="lookup", description="Synthetic tool", input_model=NoArguments, handler=lookup
    )
    invoker = NativeAgentInvoker(agent_id="custom", version="1", model_client=model, tools=tools)
    if mode == "invoke":
        output = await invoker.invoke(invocation())
    else:
        events = [event async for event in invoker.stream(invocation())]
        terminal = events[-1]
        assert terminal.type == "completed"
        output = terminal.output
    assert executions == ["lookup", "lookup"]
    assert [tool.tool_call_id for tool in output.tool_results] == ["first", "second"]
    assert [
        part.tool_call.name for part in output.messages[0].content if part.type == "tool_call"
    ] == ["lookup", "lookup"]
    assert output.usage == Usage(input_tokens=0, output_tokens=0)


def test_correspondence_ignores_object_key_order_and_allows_empty_arguments() -> None:
    result = valid_result()
    result.tool_calls[0].arguments = {"a": [None, True, 1, 1.5, "text"], "b": {}}
    part = result.message.content[0]
    assert part.type == "tool_call"
    part.tool_call = part.tool_call.model_copy(
        update={"arguments": {"b": {}, "a": [None, True, 1, 1.5, "text"]}}
    )
    validated = ModelResult(message=result.message, tool_calls=result.tool_calls)
    assert validated.tool_calls[1].arguments == {}
