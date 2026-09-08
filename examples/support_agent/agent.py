"""Native, LangChain, and LangGraph implementations behind one contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypedDict, cast

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from agent_runtime.adapters import (
    LangChainRunnableAdapter,
    LangGraphAdapter,
    NativeAgentInvoker,
)
from agent_runtime.adapters.langchain import AsyncRunnable
from agent_runtime.adapters.langgraph import AsyncGraph
from agent_runtime.adapters.mapping import messages_output
from agent_runtime.models import (
    InvocationInput,
    InvocationOutput,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
    ToolResultContent,
)
from agent_runtime.models_clients.base import ModelClient, ModelResult
from agent_runtime.registry import AgentRegistry
from agent_runtime.telemetry import RuntimeTelemetry
from agent_runtime.tools import ToolDefinition, ToolRegistry
from examples.support_agent.tools import (
    LookupOrderInput,
    StorePolicyInput,
    get_store_policy,
    lookup_order,
)


class FakeSupportModel:
    """Deterministic credential-free model for local execution and smoke tests."""

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        del tools
        last = messages[-1]
        if last.role == "tool":
            result = next(
                part.result for part in last.content if isinstance(part, ToolResultContent)
            )
            return ModelResult(
                message=Message(
                    role="assistant",
                    content=[TextContent(text=f"Synthetic tool result: {result}")],
                )
            )

        user_text = " ".join(
            part.text
            for message in reversed(messages)
            if message.role == "user"
            for part in message.content
            if isinstance(part, TextContent)
        )
        if "DEMO-" in user_text.upper():
            order_id = next(
                (
                    word.strip("?.,")
                    for word in user_text.split()
                    if word.upper().startswith("DEMO-")
                ),
                "DEMO-42",
            ).upper()
            call = ToolCall(
                id=f"lookup-{context.run_id}",
                name="lookup_order",
                arguments={"order_id": order_id},
            )
            return ModelResult(
                message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
                tool_calls=[call],
            )
        return ModelResult(
            message=Message(
                role="assistant",
                content=[
                    TextContent(text="This fictional support agent can look up DEMO-42 or DEMO-77.")
                ],
            )
        )


class SupportState(TypedDict):
    """Minimal LangGraph state with no persistent checkpointer."""

    messages: list[dict[str, object]]
    metadata: dict[str, object]
    context: dict[str, object]


def _framework_response(value: Mapping[str, object]) -> dict[str, object]:
    raw_messages = value.get("messages", [])
    count = len(raw_messages) if isinstance(raw_messages, list) else 0
    return {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": f"Framework adapter received {count} canonical messages.",
                    }
                ],
            }
        ]
    }


def _graph_node(state: SupportState) -> SupportState:
    response = _framework_response(state)
    return {
        "messages": cast(list[dict[str, object]], response["messages"]),
        "metadata": state.get("metadata", {}),
        "context": state.get("context", {}),
    }


def _only_new_messages(value: object, request: InvocationInput) -> InvocationOutput:
    output = messages_output(value, request)
    return InvocationOutput(messages=[output.messages[-1]], metadata=output.metadata)


def build_agent_registry(
    *,
    model_client: ModelClient,
    tools: ToolRegistry,
    telemetry: RuntimeTelemetry,
) -> AgentRegistry:
    """Register interchangeable native, LangChain, and LangGraph agents."""
    registry = AgentRegistry()
    registry.register(
        NativeAgentInvoker(
            agent_id="support-native",
            version="0.1.0",
            model_client=model_client,
            tools=tools,
            capabilities={"tools", "multi_turn"},
            telemetry=telemetry,
        )
    )

    runnable = RunnableLambda(_framework_response)
    registry.register(
        LangChainRunnableAdapter(
            agent_id="support-langchain",
            version="0.1.0",
            runnable=cast(AsyncRunnable, runnable),
            output_mapper=_only_new_messages,
            capabilities={"multi_turn"},
        )
    )

    builder = StateGraph(SupportState)
    builder.add_node("respond", _graph_node)
    builder.add_edge(START, "respond")
    builder.add_edge("respond", END)
    graph = builder.compile()
    registry.register(
        LangGraphAdapter(
            agent_id="support-langgraph",
            version="0.1.0",
            graph=cast(AsyncGraph, graph),
            state_output_mapper=_only_new_messages,
            capabilities={"multi_turn"},
        )
    )
    return registry


def build_tool_registry(*, telemetry: RuntimeTelemetry) -> ToolRegistry:
    """Register synthetic example tools."""
    registry = ToolRegistry(telemetry=telemetry)
    registry.register(
        name="lookup_order",
        description="Look up a fictional order by its DEMO identifier.",
        input_model=LookupOrderInput,
        handler=lookup_order,
    )
    registry.register(
        name="get_store_policy",
        description="Read a fictional store policy by topic.",
        input_model=StorePolicyInput,
        handler=get_store_policy,
    )
    return registry
