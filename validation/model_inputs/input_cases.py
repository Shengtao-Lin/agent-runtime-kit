"""Small application-owned model/tool growth fixtures using only installed public APIs."""

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.context import ContextWindow
from agent_runtime.hooks import HookManager
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import ToolCall, ToolCallContent
from agent_runtime.models_clients.base import ModelCompleted, ModelResult, ModelTextDelta
from agent_runtime.runs import PostgresRunStore
from agent_runtime.tools import ToolRegistry

CASES = {
    "oversized_tool": {"rounds": 1, "calls_per_round": 1, "payload_bytes": 256 * 1024},
    "multiple_tools": {"rounds": 1, "calls_per_round": 3, "payload_bytes": 24 * 1024},
    "multiple_rounds": {"rounds": 3, "calls_per_round": 1, "payload_bytes": 24 * 1024},
    "hook_growth": {"rounds": 0, "calls_per_round": 0, "payload_bytes": 0},
    "tool_definitions": {"rounds": 0, "calls_per_round": 0, "payload_bytes": 0},
}


def canonical_bytes(values):
    return len(
        json.dumps(
            [value.model_dump(mode="json") for value in values],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


class PayloadInput(BaseModel):
    size: int = Field(ge=0, le=256 * 1024)


class LargeDefinitionInput(PayloadInput):
    size: int = Field(ge=0, le=256 * 1024, description="x" * 9000)


class InputGrowthHook:
    name = "host-input-growth"

    async def handle(self, event):
        if event.phase == "before_model":
            # ContextWindow copies selected messages, so the original durable request stays intact.
            event.messages[-1].content.extend(TextContent(text="x" * 24000) for _ in range(3))


class ProbeModel:
    def __init__(self, settings):
        self.settings = settings
        self.calls = []
        self.opened_streams = 0
        self.closed_streams = 0

    async def generate(self, messages, *, tools=(), context):
        self.calls.append(
            {
                "message_bytes": canonical_bytes(messages),
                "tool_bytes": canonical_bytes(tools),
                "initial_selected_bytes": context.attributes["agent_runtime.context"][
                    "selected_bytes"
                ],
            }
        )
        index = len(self.calls)
        if index <= self.settings["rounds"]:
            calls = [
                ToolCall(
                    id=f"round-{index}-call-{n}",
                    name="emit",
                    arguments={"size": self.settings["payload_bytes"]},
                )
                for n in range(self.settings["calls_per_round"])
            ]
            return ModelResult(
                message=Message(
                    role="assistant",
                    content=[
                        TextContent(text="working"),
                        *[ToolCallContent(tool_call=call) for call in calls],
                    ],
                ),
                tool_calls=calls,
            )
        return ModelResult(message=Message(role="assistant", content=[TextContent(text="done")]))

    async def stream(self, messages, *, tools=(), context):
        self.opened_streams += 1
        try:
            result = await self.generate(messages, tools=tools, context=context)
            for part in result.message.content:
                if isinstance(part, TextContent):
                    yield ModelTextDelta(delta=part.text)
            yield ModelCompleted(result=result)
        finally:
            self.closed_streams += 1


@dataclass
class GrowthHost:
    runtime: AgentRuntime
    memory: PostgresMemoryStore
    runs: PostgresRunStore
    model: ProbeModel
    effects: list[int]
    namespace: str
    cleanup: dict

    def request(self):
        return RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="review")])])


@asynccontextmanager
async def growth_host(database_url, case):
    namespace = f"stage-f-growth-{uuid4()}"
    engine = create_async_engine(
        database_url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"application_name": namespace},
    )
    model = ProbeModel(CASES[case])
    effects, cleanup = [], {}
    tools = ToolRegistry()

    async def emit(size):
        effects.append(size)  # A host-visible effect is not rolled back by a later runtime failure.
        return {"payload": "x" * size}

    tools.register(
        name="emit",
        description="Return a bounded synthetic review payload",
        input_model=LargeDefinitionInput if case == "tool_definitions" else PayloadInput,
        handler=emit,
    )
    agents = AgentRegistry()
    agents.register(
        NativeAgentInvoker(
            agent_id=namespace, version="1", model_client=model, tools=tools, max_tool_iterations=3
        )
    )
    memory, runs = PostgresMemoryStore(engine), PostgresRunStore(engine)
    runtime = AgentRuntime(
        agents=agents,
        memory=memory,
        runs=runs,
        namespace=namespace,
        context_window=ContextWindow(65536),
        stream_history=True,
        invocation_timeout_seconds=30,
        hooks=HookManager([InputGrowthHook()] if case == "hook_growth" else []),
    )
    try:
        async with runtime.lifespan(shutdown_timeout_seconds=6):
            yield GrowthHost(runtime, memory, runs, model, effects, namespace, cleanup)
    finally:
        cleanup["pool_checkouts"] = engine.pool.checkedout()
        await engine.dispose()
        control = create_async_engine(database_url)
        try:
            async with control.connect() as connection:
                cleanup["database_sessions"] = await connection.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE application_name=:name"),
                    {"name": namespace},
                )
        finally:
            await control.dispose()
