"""Application-owned framework execution; imports only installed public kit APIs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime
from agent_runtime.adapters import LangChainRunnableAdapter, LangGraphAdapter
from agent_runtime.adapters.framework_messages import framework_input, framework_output
from agent_runtime.feedback import PostgresFeedbackStore
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import InvocationInput, RuntimeContext
from agent_runtime.runs import PostgresRunStore
from agent_runtime.tools import ToolRegistry


class QuoteInput(BaseModel):
    units: int


class QuoteState(TypedDict):
    """State belongs to the host application, including its routing field."""

    transcript: list[BaseMessage]
    context: RuntimeContext
    phase: str


@dataclass
class FrameworkApplication:
    runtime: AgentRuntime
    memory: PostgresMemoryStore
    runs: PostgresRunStore
    feedback: PostgresFeedbackStore
    agent_id: str
    seen_history: list[int]
    tool_calls: list[int]
    entered: asyncio.Event
    cancelled: asyncio.Event


@asynccontextmanager
async def application(
    database_url: str, identity: str, framework: Literal["langchain", "langgraph"]
) -> AsyncIterator[FrameworkApplication]:
    engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    feedback = PostgresFeedbackStore(engine)
    tools = ToolRegistry()
    seen: list[int] = []
    calls: list[int] = []
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def quote(units: int) -> dict[str, int]:
        calls.append(units)
        return {"cents": units * 125}

    tools.register(
        name="quote", description="Synthetic price quote", input_model=QuoteInput, handler=quote
    )

    def input_mapper(request: InvocationInput) -> QuoteState:
        return {
            "transcript": framework_input(request)["messages"],
            "context": request.context,
            "phase": "model",
        }

    def output_mapper(state: QuoteState, request: InvocationInput):
        return framework_output({"messages": state["transcript"]}, request)

    async def model(state: QuoteState) -> QuoteState:
        history = state["transcript"]
        last = history[-1]
        if isinstance(last, HumanMessage):
            seen.append(len(history))
            text = (
                last.content
                if isinstance(last.content, str)
                else "".join(
                    block if isinstance(block, str) else block.get("text", "")
                    for block in last.content
                )
            )
            if text == "wait":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            if text == "fail":
                raise ValueError("private-framework-failure")
            answer = AIMessage(
                id=str(uuid4()),
                content="",
                tool_calls=[{"id": str(uuid4()), "name": "quote", "args": {"units": 3}}],
            )
            phase = "tool"
        else:
            assert isinstance(last, ToolMessage)
            answer = AIMessage(id=str(uuid4()), content=f"Quote: {last.content}")
            phase = "done"
        return {**state, "transcript": [*history, answer], "phase": phase}

    async def execute_tool(state: QuoteState) -> QuoteState:
        message = state["transcript"][-1]
        assert isinstance(message, AIMessage)
        call = message.tool_calls[0]
        result = await tools.invoke(
            call["name"], call["args"], tool_call_id=call["id"], context=state["context"]
        )
        tool_message = ToolMessage(
            id=str(result.id),
            name=result.tool_name,
            tool_call_id=call["id"],
            content=str(result.output["cents"]),
        )
        return {**state, "transcript": [*state["transcript"], tool_message], "phase": "model"}

    async def runnable(state: QuoteState) -> QuoteState:
        state = await model(state)
        state = await execute_tool(state)
        return await model(state)

    agents = AgentRegistry()
    if framework == "langchain":
        adapter = LangChainRunnableAdapter(
            agent_id=identity,
            version="1",
            runnable=RunnableLambda(runnable),
            input_mapper=input_mapper,
            output_mapper=output_mapper,
        )
    else:
        graph = StateGraph(QuoteState)
        graph.add_node("model", model)
        graph.add_node("tool", execute_tool)
        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model", lambda state: state["phase"], {"tool": "tool", "done": END}
        )
        graph.add_edge("tool", "model")
        adapter = LangGraphAdapter(
            agent_id=identity,
            version="1",
            graph=graph.compile(),
            state_input_mapper=input_mapper,
            state_output_mapper=output_mapper,
        )
    agents.register(adapter)
    runtime = AgentRuntime(
        agents=agents,
        memory=memory,
        runs=runs,
        namespace=identity,
        invocation_timeout_seconds=5,
    )
    try:
        yield FrameworkApplication(
            runtime, memory, runs, feedback, identity, seen, calls, entered, cancelled
        )
    finally:
        try:
            if not await runtime.shutdown(timeout_seconds=6):
                raise RuntimeError("Framework application shutdown did not finish")
        finally:
            await engine.dispose()
