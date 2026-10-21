"""A downstream application composed entirely from installed public APIs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.feedback import PostgresFeedbackStore
from agent_runtime.hooks import HookManager
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import RuntimeContext, ToolCall, ToolCallContent
from agent_runtime.models_clients.base import (
    ModelCompleted,
    ModelResult,
    ModelStreamEvent,
    ModelTextDelta,
)
from agent_runtime.runs import PostgresRunStore
from agent_runtime.telemetry import TelemetryHandle, configure_telemetry
from agent_runtime.tools import ToolDefinition, ToolRegistry


def request(text: str, **kwargs: object) -> RuntimeRequest:
    """Create new caller IDs while preserving semantic input for replay."""
    return RuntimeRequest.model_validate(
        {"messages": [Message(role="user", content=[TextContent(text=text)])], **kwargs}
    )


class DoubleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


class ConsumerModel:
    """Credential-free application model with tools, streaming, and cancellation probes."""

    def __init__(self) -> None:
        self.calls: list[tuple[Message, ...]] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> ModelResult:
        del tools
        self.calls.append(tuple(messages))
        last = messages[-1]
        text = " ".join(part.text for part in last.content if isinstance(part, TextContent))
        if text == "wait":
            self.started.set()
            await self.release.wait()
        if text == "double 7":
            call = ToolCall(id=f"double-{context.run_id}", name="double", arguments={"value": 7})
            return ModelResult(
                message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
                tool_calls=[call],
            )
        answer = (
            "x" * 1024
            if text == "slow stream"
            else "Result: 14"
            if last.role == "tool"
            else f"Saw {len(messages)} messages"
        )
        return ModelResult(message=Message(role="assistant", content=[TextContent(text=answer)]))

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolDefinition] = (),
        context: RuntimeContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        result = await self.generate(messages, tools=tools, context=context)
        for part in result.message.content:
            if isinstance(part, TextContent):
                if part.text == "x" * 1024:
                    for offset in range(0, len(part.text), 8):
                        yield ModelTextDelta(delta=part.text[offset : offset + 8])
                else:
                    midpoint = len(part.text) // 2
                    yield ModelTextDelta(delta=part.text[:midpoint])
                    yield ModelTextDelta(delta=part.text[midpoint:])
        yield ModelCompleted(result=result)

    async def close(self) -> None:
        self.closed = True


@dataclass
class ConsumerApplication:
    agent_id: str
    runtime: AgentRuntime
    memory: PostgresMemoryStore
    runs: PostgresRunStore
    feedback: PostgresFeedbackStore
    model: ConsumerModel
    engine: AsyncEngine
    telemetry: TelemetryHandle


@asynccontextmanager
async def application(
    database_url: str,
    identity: str,
    *,
    invocation_deadline_seconds: float = 2.0,
    hooks: HookManager | None = None,
) -> AsyncIterator[ConsumerApplication]:
    """Own the model, engine, and telemetry for one application lifetime."""
    engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    telemetry = configure_telemetry(service_name="independent-consumer", capture_content=False)
    model = ConsumerModel()
    try:
        memory = PostgresMemoryStore(engine, telemetry.telemetry)
        runs = PostgresRunStore(engine)
        feedback = PostgresFeedbackStore(engine, telemetry.telemetry)
        tools = ToolRegistry(hooks=hooks, telemetry=telemetry.telemetry)
        tools.register(
            name="double",
            description="Double one synthetic integer.",
            input_model=DoubleInput,
            handler=lambda value: {"value": value * 2},
            timeout_seconds=1,
        )
        agents = AgentRegistry()
        agents.register(
            NativeAgentInvoker(
                agent_id=identity,
                version="1.0.0",
                model_client=model,
                tools=tools,
                capabilities={"streaming", "tools"},
                telemetry=telemetry.telemetry,
            )
        )
        runtime = AgentRuntime(
            agents=agents,
            memory=memory,
            runs=runs,
            namespace=identity,
            hooks=hooks,
            invocation_timeout_seconds=invocation_deadline_seconds,
            telemetry=telemetry.telemetry,
        )
        yield ConsumerApplication(
            identity, runtime, memory, runs, feedback, model, engine, telemetry
        )
    finally:
        try:
            await model.close()
        finally:
            try:
                await engine.dispose()
            finally:
                telemetry.shutdown()
