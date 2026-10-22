"""Invocation usage includes every model call or remains explicitly unknown."""

from collections.abc import Sequence
from typing import Literal
from uuid import uuid4

import pytest
from pydantic import BaseModel

from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.models import (
    InvocationInput,
    InvocationOutput,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
    Usage,
)
from agent_runtime.models_clients import DeterministicModelClient, ModelResult
from agent_runtime.tools import ToolDefinition, ToolRegistry

Mode = Literal["invoke", "stream", "stream_fallback"]


class EmptyInput(BaseModel):
    pass


class GenerateOnlyClient:
    """Expose only the required model protocol to exercise stream fallback."""

    def __init__(self, delegate: DeterministicModelClient) -> None:
        self.delegate = delegate

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        return await self.delegate.generate(messages, tools=tools, context=context)


def results_for(usages: list[Usage | None]) -> list[ModelResult]:
    results: list[ModelResult] = []
    for index, usage in enumerate(usages):
        if index == len(usages) - 1:
            results.append(
                ModelResult(
                    message=Message(role="assistant", content=[TextContent(text="Done")]),
                    usage=usage,
                )
            )
        else:
            call = ToolCall(id=f"lookup-{index}", name="lookup", arguments={})
            results.append(
                ModelResult(
                    message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
                    tool_calls=[call],
                    usage=usage,
                )
            )
    return results


def invoker_for(model: DeterministicModelClient, mode: Mode) -> NativeAgentInvoker:
    tools = ToolRegistry()
    tools.register(
        name="lookup",
        description="Return a synthetic value.",
        input_model=EmptyInput,
        handler=lambda: "found",
    )
    return NativeAgentInvoker(
        agent_id="usage-test",
        version="1",
        model_client=GenerateOnlyClient(model) if mode == "stream_fallback" else model,
        tools=tools,
    )


async def output_for(invoker: NativeAgentInvoker, mode: Mode) -> InvocationOutput:
    request = InvocationInput(
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        messages=[Message(role="user", content=[TextContent(text="Look up the value")])],
    )
    if mode == "invoke":
        return await invoker.invoke(request)
    events = [event async for event in invoker.stream(request)]
    completed = [event for event in events if event.type == "completed"]
    assert len(completed) == 1
    assert events[-1] is completed[0]
    return completed[0].output


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invoke", "stream", "stream_fallback"])
@pytest.mark.parametrize(
    ("usages", "expected"),
    [
        pytest.param(
            [Usage(input_tokens=3, output_tokens=7)],
            Usage(input_tokens=3, output_tokens=7),
            id="single-call",
        ),
        pytest.param(
            [Usage(input_tokens=3, output_tokens=7), Usage(input_tokens=11, output_tokens=13)],
            Usage(input_tokens=14, output_tokens=20),
            id="tool-round-trip",
        ),
        pytest.param(
            [
                Usage(input_tokens=1, output_tokens=2),
                Usage(input_tokens=3, output_tokens=4),
                Usage(input_tokens=5, output_tokens=6),
            ],
            Usage(input_tokens=9, output_tokens=12),
            id="multiple-tool-rounds",
        ),
        pytest.param([None, Usage(input_tokens=3, output_tokens=7)], None, id="unknown-first"),
        pytest.param(
            [
                Usage(input_tokens=3, output_tokens=7),
                None,
                Usage(input_tokens=11, output_tokens=13),
            ],
            None,
            id="unknown-middle",
        ),
        pytest.param([Usage(input_tokens=3, output_tokens=7), None], None, id="unknown-last"),
        pytest.param([None, None], None, id="all-unknown"),
        pytest.param([None], None, id="single-unknown"),
        pytest.param(
            [Usage(input_tokens=0, output_tokens=0), Usage(input_tokens=0, output_tokens=0)],
            Usage(input_tokens=0, output_tokens=0),
            id="explicit-zero",
        ),
        pytest.param(
            [Usage(input_tokens=0, output_tokens=0), Usage(input_tokens=3, output_tokens=7)],
            Usage(input_tokens=3, output_tokens=7),
            id="zero-and-known",
        ),
    ],
)
async def test_native_usage_covers_all_model_calls(
    mode: Mode, usages: list[Usage | None], expected: Usage | None
) -> None:
    scripted = results_for(usages)
    original = [result.model_copy(deep=True) for result in scripted]
    model = DeterministicModelClient(scripted)

    output = await output_for(invoker_for(model, mode), mode)

    assert len(model.calls) == len(usages)
    assert len(output.tool_results) == len(usages) - 1
    assert output.usage == expected
    assert scripted == original


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invoke", "stream", "stream_fallback"])
async def test_native_usage_is_scoped_to_one_invocation(mode: Mode) -> None:
    model = DeterministicModelClient(
        results_for([None, Usage(input_tokens=1, output_tokens=2)])
        + results_for(
            [Usage(input_tokens=3, output_tokens=4), Usage(input_tokens=5, output_tokens=6)]
        )
    )
    invoker = invoker_for(model, mode)

    first = await output_for(invoker, mode)
    second = await output_for(invoker, mode)

    assert first.usage is None
    assert second.usage == Usage(input_tokens=8, output_tokens=10)
    assert len(model.calls) == 4
