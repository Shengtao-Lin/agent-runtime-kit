"""Native, LangChain, and LangGraph implementations behind one contract."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from math import isfinite
from typing import TypedDict, cast

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from agent_runtime.adapters import (
    LangChainRunnableAdapter,
    LangGraphAdapter,
    NativeAgentInvoker,
)
from agent_runtime.adapters.framework_messages import (
    framework_input,
    framework_output,
    from_framework_message,
    to_framework_message,
)
from agent_runtime.adapters.langchain import AsyncRunnable
from agent_runtime.adapters.langgraph import AsyncGraph
from agent_runtime.errors import MaxToolIterationsError
from agent_runtime.models import (
    InvocationInput,
    Message,
    RuntimeContext,
    TextContent,
    ToolCall,
    ToolCallContent,
    ToolResultContent,
)
from agent_runtime.models_clients.base import (
    ModelClient,
    ModelCompleted,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
)
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

    def __init__(self, *, delay_seconds: float = 0) -> None:
        if not isfinite(delay_seconds) or delay_seconds < 0:
            raise ValueError("delay_seconds must be finite and nonnegative")
        self._delay_seconds = delay_seconds

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        del tools
        if self._delay_seconds:
            logging.getLogger(__name__).info(
                "demo_model_delay_started", extra={"run_id": str(context.run_id)}
            )
            await asyncio.sleep(self._delay_seconds)
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

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Stream deterministic text without requiring provider credentials."""
        result = await self.generate(messages, tools=tools, context=context)
        for part in result.message.content:
            if isinstance(part, TextContent):
                yield ModelTextDelta(delta=part.text)
        yield ModelCompleted(result=result)


class SupportState(TypedDict):
    """Minimal LangGraph state with no persistent checkpointer."""

    messages: list[BaseMessage]
    metadata: dict[str, object]
    context: dict[str, object]
    tool_iterations: int
    tool_results: list[dict[str, object]]
    usage: dict[str, object] | None


def _framework_input(request: InvocationInput) -> SupportState:
    value = framework_input(request)
    return {
        "messages": cast(list[BaseMessage], value["messages"]),
        "context": request.context.model_dump(mode="json"),
        "metadata": request.metadata,
        "tool_iterations": 0,
        "tool_results": [],
        "usage": None,
    }


def build_agent_registry(
    *,
    model_client: ModelClient,
    tools: ToolRegistry,
    telemetry: RuntimeTelemetry,
    max_tool_iterations: int = 3,
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
            max_tool_iterations=max_tool_iterations,
        )
    )

    async def model_step(state: SupportState) -> SupportState:
        context = RuntimeContext.model_validate(state["context"])
        messages = [from_framework_message(message) for message in state["messages"]]
        attributes: dict[str, object] = {
            "agent.run.id": str(context.run_id),
            "agent.message.count": len(messages),
        }
        attributes.update(telemetry.content_attributes(messages))
        with telemetry.span("agent.model.generate", attributes):
            result = await model_client.generate(
                messages, tools=tools.definitions(), context=context
            )
        return {
            **state,
            "messages": [*state["messages"], to_framework_message(result.message)],
            "usage": result.usage.model_dump(mode="json") if result.usage else None,
        }

    async def tool_step(state: SupportState) -> SupportState:
        if state["tool_iterations"] >= max_tool_iterations:
            raise MaxToolIterationsError("Example exceeded its configured tool iteration limit")
        assistant = state["messages"][-1]
        assert isinstance(assistant, AIMessage)
        messages = list(state["messages"])
        results = list(state["tool_results"])
        for call in assistant.tool_calls:
            result = await tools.invoke(
                call["name"],
                call["args"],
                tool_call_id=call["id"] or "",
                context=RuntimeContext.model_validate(state["context"]),
            )
            message = Message(
                role="tool",
                content=[
                    ToolResultContent(
                        tool_call_id=result.tool_call_id,
                        result=result.output,
                    )
                ],
            )
            native = to_framework_message(message)
            assert isinstance(native, ToolMessage)
            native.name = result.tool_name
            messages.append(native)
            results.append(result.model_dump(mode="json"))
        return {
            **state,
            "messages": messages,
            "tool_results": results,
            "tool_iterations": state["tool_iterations"] + 1,
        }

    def route(state: SupportState) -> str:
        message = state["messages"][-1]
        return "tools" if isinstance(message, AIMessage) and message.tool_calls else END

    async def runnable_loop(state: SupportState) -> SupportState:
        while True:
            state = await model_step(state)
            if route(state) == END:
                return state
            state = await tool_step(state)

    runnable = RunnableLambda(runnable_loop)
    registry.register(
        LangChainRunnableAdapter(
            agent_id="support-langchain",
            version="0.1.0",
            runnable=cast(AsyncRunnable, runnable),
            input_mapper=_framework_input,
            output_mapper=framework_output,
            capabilities={"tools", "multi_turn"},
        )
    )

    builder = StateGraph(SupportState)
    builder.add_node("model", model_step)
    builder.add_node("tools", tool_step)
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route, {"tools": "tools", END: END})
    builder.add_edge("tools", "model")
    graph = builder.compile().with_config({"recursion_limit": 2 * max_tool_iterations + 5})
    registry.register(
        LangGraphAdapter(
            agent_id="support-langgraph",
            version="0.1.0",
            graph=cast(AsyncGraph, graph),
            state_input_mapper=_framework_input,
            state_output_mapper=framework_output,
            capabilities={"tools", "multi_turn"},
        )
    )
    return registry


def build_tool_registry(
    *, telemetry: RuntimeTelemetry, timeout_seconds: float = 10
) -> ToolRegistry:
    """Register synthetic example tools."""
    registry = ToolRegistry(telemetry=telemetry)
    registry.register(
        name="lookup_order",
        description="Look up a fictional order by its DEMO identifier.",
        input_model=LookupOrderInput,
        handler=lookup_order,
        timeout_seconds=timeout_seconds,
    )
    registry.register(
        name="get_store_policy",
        description="Read a fictional store policy by topic.",
        input_model=StorePolicyInput,
        handler=get_store_policy,
        timeout_seconds=timeout_seconds,
    )
    return registry
