from typing import Any, cast
from uuid import uuid4

import pytest

from agent_runtime.adapters import LangChainRunnableAdapter, LangGraphAdapter
from agent_runtime.errors import AgentInvocationError
from agent_runtime.models import InvocationInput, Message, RuntimeContext, TextContent


class EchoRunnable:
    def __init__(self) -> None:
        self.received: object | None = None

    async def ainvoke(self, input: object) -> object:
        self.received = input
        return {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Canonical reply"}],
                }
            ]
        }


class FailingRunnable:
    async def ainvoke(self, input: object) -> object:
        del input
        raise ValueError("framework details")


def invocation_input() -> InvocationInput:
    return InvocationInput(
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
        messages=[Message(role="user", content=[TextContent(text="Hello")])],
    )


@pytest.mark.asyncio
async def test_langchain_and_langgraph_produce_equivalent_canonical_output() -> None:
    runnable = EchoRunnable()
    graph = EchoRunnable()
    langchain = LangChainRunnableAdapter(agent_id="lc-agent", version="1", runnable=runnable)
    langgraph = LangGraphAdapter(agent_id="lg-agent", version="1", graph=graph)

    lc_output = await langchain.invoke(invocation_input())
    lg_output = await langgraph.invoke(invocation_input())

    assert lc_output.model_dump(exclude={"messages": {0: {"id"}}}) == lg_output.model_dump(
        exclude={"messages": {0: {"id"}}}
    )
    assert isinstance(runnable.received, dict)
    assert isinstance(graph.received, dict)


@pytest.mark.asyncio
async def test_explicit_adapter_mappers_support_custom_schemas() -> None:
    runnable = EchoRunnable()

    def input_mapper(value: InvocationInput) -> object:
        return {"prompt": value.messages[-1].content[0].model_dump()}

    def output_mapper(value: object, request: InvocationInput) -> Any:
        del value
        from agent_runtime.models import InvocationOutput

        return InvocationOutput(messages=[request.messages[-1]])

    adapter = LangChainRunnableAdapter(
        agent_id="custom",
        version="1",
        runnable=runnable,
        input_mapper=input_mapper,
        output_mapper=output_mapper,
    )
    output = await adapter.invoke(invocation_input())
    assert output.messages[0].role == "user"
    received_value = cast(object, runnable.received)
    assert isinstance(received_value, dict)
    received = cast(dict[str, object], received_value)
    assert "prompt" in received


@pytest.mark.asyncio
async def test_framework_exceptions_are_mapped_to_canonical_error() -> None:
    adapter = LangGraphAdapter(agent_id="broken", version="1", graph=FailingRunnable())
    with pytest.raises(AgentInvocationError, match="LangGraph"):
        await adapter.invoke(invocation_input())
