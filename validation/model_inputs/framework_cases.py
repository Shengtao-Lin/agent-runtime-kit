"""Explicit host model boundaries inside real Runnable and compiled graph execution."""

from typing import TypedDict

from host_policy import ModelInputBudget
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph

from agent_runtime import Message, TextContent
from agent_runtime.adapters import LangChainRunnableAdapter, LangGraphAdapter
from agent_runtime.adapters.base import AgentCompleted, AgentTextDelta
from agent_runtime.models import InvocationOutput, ToolResultContent


class State(TypedDict):
    messages: list
    generated: list
    results: list
    context: object
    phase: str
    calls: list


def framework_invoker(
    namespace,
    framework,
    delegate,
    tools,
    *,
    entry_only=False,
    mapper_growth=False,
    model_stream=False,
):
    """Only this explicit delegate is protected; the framework owns graph/tool routing."""

    def input_mapper(request):
        if entry_only:
            ModelInputBudget(65536, 8192).check(request.messages, tools.definitions())
        return State(
            messages=list(request.messages),
            generated=[],
            results=[],
            context=request.context,
            phase="model",
            calls=[],
        )

    def output_mapper(state, request):
        return InvocationOutput(messages=state["generated"], tool_results=state["results"])

    async def model(state):
        # The host's final mapping step occurs before its protected actual model delegate.
        messages = list(state["messages"])
        if mapper_growth:
            messages.append(
                Message(role="system", content=[TextContent(text="x" * 24000) for _ in range(3)])
            )
        if model_stream:
            source = delegate.stream(messages, tools=tools.definitions(), context=state["context"])
            result = None
            try:
                async for event in source:
                    if event.type == "completed":
                        result = event.result
            finally:
                await source.aclose()
            assert result is not None
        else:
            result = await delegate.generate(
                messages, tools=tools.definitions(), context=state["context"]
            )
        return {
            **state,
            "messages": [*state["messages"], result.message],
            "generated": [*state["generated"], result.message],
            "phase": "tool" if result.tool_calls else "done",
            "calls": result.tool_calls,
        }

    async def execute_tools(state):
        generated, results = list(state["generated"]), list(state["results"])
        messages = list(state["messages"])
        for call in state["calls"]:
            # Explicit host reuse of ToolRegistry; no interception of framework-native tools.
            result = await tools.invoke(
                call.name, call.arguments, tool_call_id=call.id, context=state["context"]
            )
            message = Message(
                role="tool", content=[ToolResultContent(tool_call_id=call.id, result=result.output)]
            )
            generated.append(message)
            messages.append(message)
            results.append(result)
        return {
            **state,
            "messages": messages,
            "generated": generated,
            "results": results,
            "phase": "model",
        }

    async def runnable(state):
        for _ in range(4):
            state = await model(state)
            yield state
            if state["phase"] == "done":
                return
            state = await execute_tools(state)
            yield state
        raise AssertionError("Fixture exceeded its frozen tool-round bound")

    async def stream_mapper(source, request):
        final = None
        seen = 0
        async for item in source:
            if framework == "langgraph":
                kind, state = item
                if kind != "values":
                    continue
            else:
                state = item
            for message in state["generated"][seen:]:
                for part in message.content:
                    if isinstance(part, TextContent):
                        yield AgentTextDelta(delta=part.text)
            seen = len(state["generated"])
            final = state
        assert final is not None and final["phase"] == "done"
        yield AgentCompleted(output=output_mapper(final, request))

    if framework == "langchain":
        return LangChainRunnableAdapter(
            agent_id=namespace,
            version="1",
            runnable=RunnableLambda(runnable),
            input_mapper=input_mapper,
            output_mapper=output_mapper,
            stream_mapper=stream_mapper,
        )
    graph = StateGraph(State)
    graph.add_node("model", model)
    graph.add_node("tool", execute_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model", lambda state: state["phase"], {"tool": "tool", "done": END}
    )
    graph.add_edge("tool", "model")
    return LangGraphAdapter(
        agent_id=namespace,
        version="1",
        graph=graph.compile(),
        state_input_mapper=input_mapper,
        state_output_mapper=output_mapper,
        stream_mapper=stream_mapper,
    )
