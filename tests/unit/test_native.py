from uuid import uuid4

import pytest
from pydantic import BaseModel

from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.errors import MaxToolIterationsError, ProviderError
from agent_runtime.models import (
    InvocationInput,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
)
from agent_runtime.models_clients import DeterministicModelClient, ModelResult
from agent_runtime.tools import ToolRegistry


class LookupInput(BaseModel):
    order_id: str


def lookup_order(order_id: str) -> dict[str, str]:
    return {"order_id": order_id, "status": "shipped"}


def echo_order_id(order_id: str) -> str:
    return order_id


def request() -> InvocationInput:
    return InvocationInput(
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        messages=[Message(role="user", content=[TextContent(text="Find DEMO-42")])],
    )


@pytest.mark.asyncio
async def test_native_invoker_executes_tool_and_returns_final_message() -> None:
    call = ToolCall(id="call-1", name="lookup_order", arguments={"order_id": "DEMO-42"})
    model = DeterministicModelClient(
        [
            ModelResult(
                message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
                tool_calls=[call],
            ),
            ModelResult(
                message=Message(role="assistant", content=[TextContent(text="It shipped")])
            ),
        ]
    )
    tools = ToolRegistry()
    tools.register(
        name="lookup_order",
        description="Look up a synthetic order.",
        input_model=LookupInput,
        handler=lookup_order,
    )
    invoker = NativeAgentInvoker(agent_id="support", version="1", model_client=model, tools=tools)

    output = await invoker.invoke(request())

    assert len(model.calls) == 2
    assert len(output.tool_results) == 1
    assert output.tool_results[0].output["status"] == "shipped"
    final_content = output.messages[-1].content[0]
    assert isinstance(final_content, TextContent)
    assert final_content.text == "It shipped"
    assert model.calls[1][-1].role == "tool"


@pytest.mark.asyncio
async def test_native_invoker_enforces_tool_iteration_limit() -> None:
    call = ToolCall(id="call-1", name="lookup_order", arguments={"order_id": "DEMO-42"})
    looping = ModelResult(
        message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
        tool_calls=[call],
    )
    tools = ToolRegistry()
    tools.register(
        name="lookup_order",
        description="Look up.",
        input_model=LookupInput,
        handler=echo_order_id,
    )
    invoker = NativeAgentInvoker(
        agent_id="loop",
        version="1",
        model_client=DeterministicModelClient([looping]),
        tools=tools,
        max_tool_iterations=1,
    )
    with pytest.raises(MaxToolIterationsError):
        await invoker.invoke(request())


@pytest.mark.asyncio
async def test_native_invoker_hides_provider_exception() -> None:
    class FailingModel:
        async def generate(self, messages: object, **kwargs: object) -> ModelResult:
            del messages, kwargs
            raise ValueError("provider response")

    invoker = NativeAgentInvoker(agent_id="failure", version="1", model_client=FailingModel())
    with pytest.raises(ProviderError, match="Model generation failed"):
        await invoker.invoke(request())
