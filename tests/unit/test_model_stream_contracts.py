"""Custom model streams cannot dispatch tools from ambiguous terminal results."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Literal, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel

from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.adapters.base import AgentStreamEvent
from agent_runtime.errors import ProviderError
from agent_runtime.models import (
    InvocationInput,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
)
from agent_runtime.models_clients import (
    ModelCompleted,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
)
from agent_runtime.tools import ToolDefinition, ToolRegistry

Shape = Literal["valid", "missing", "duplicate", "late_text", "late_failure", "blocked"]


class NoArguments(BaseModel):
    pass


class CustomModel:
    def __init__(self, shape: Shape) -> None:
        self.shape = shape
        self.closed = 0
        self.calls = 0

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        raise AssertionError("Streaming must not call generate")

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        del messages, tools, context
        self.calls += 1
        call = ToolCall(id="call-1", name="lookup", arguments={})
        result = ModelResult(
            message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
            tool_calls=[call],
        )
        try:
            yield ModelTextDelta(delta="provisional")
            if self.shape == "blocked":
                await asyncio.Event().wait()
            if self.shape == "missing":
                return
            if self.calls > 1:
                yield ModelCompleted(
                    result=ModelResult(
                        message=Message(role="assistant", content=[TextContent(text="done")])
                    )
                )
                return
            yield ModelCompleted(result=result)
            if self.shape == "duplicate":
                yield ModelCompleted(result=result)
            elif self.shape == "late_text":
                yield ModelTextDelta(delta="PRIVATE-SENTINEL")
            elif self.shape == "late_failure":
                raise ValueError("PRIVATE-SENTINEL")
        finally:
            self.closed += 1


def invocation() -> InvocationInput:
    return InvocationInput(
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        messages=[Message(role="user", content=[TextContent(text="lookup")])],
    )


def invoker(model: CustomModel, executions: list[str]) -> NativeAgentInvoker:
    tools = ToolRegistry()
    tools.register(
        name="lookup",
        description="Synthetic tool",
        input_model=NoArguments,
        handler=lambda: executions.append("lookup"),
    )
    return NativeAgentInvoker(agent_id="custom", version="1", model_client=model, tools=tools)


@pytest.mark.parametrize("shape", ["missing", "duplicate", "late_text", "late_failure"])
async def test_invalid_model_stream_never_executes_tools(shape: Shape) -> None:
    model = CustomModel(shape)
    executions: list[str] = []
    seen: list[str] = []
    with pytest.raises(ProviderError) as caught:
        async for event in invoker(model, executions).stream(invocation()):
            seen.append(event.type)
    assert "PRIVATE-SENTINEL" not in caught.value.public_message
    if shape != "late_failure":
        assert caught.value.retryable is False
    assert "completed" not in seen
    assert executions == []
    assert model.closed == model.calls == 1


async def test_valid_model_stream_finishes_before_tool_dispatch() -> None:
    model = CustomModel("valid")
    executions: list[str] = []
    events = [event async for event in invoker(model, executions).stream(invocation())]
    assert events[-1].type == "completed"
    assert executions == ["lookup"]
    assert model.closed == model.calls == 2


async def test_abandoned_native_stream_closes_custom_model() -> None:
    model = CustomModel("blocked")
    executions: list[str] = []
    stream = cast(
        AsyncGenerator[AgentStreamEvent, None], invoker(model, executions).stream(invocation())
    )
    assert (await anext(stream)).type == "text_delta"
    await stream.aclose()
    assert model.closed == 1
    assert executions == []


async def test_cancelled_native_stream_closes_custom_model() -> None:
    model = CustomModel("blocked")
    stream = invoker(model, []).stream(invocation())
    assert (await anext(stream)).type == "text_delta"
    waiting = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert model.closed == 1
