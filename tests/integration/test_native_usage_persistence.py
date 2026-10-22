"""Invocation usage survives durable completion and replay without executing again."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, RuntimeRequest
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import Message, TextContent, ToolCall, ToolCallContent, Usage
from agent_runtime.models_clients import DeterministicModelClient, ModelResult
from agent_runtime.runs import PostgresRunStore
from agent_runtime.tools import ToolRegistry

pytestmark = pytest.mark.integration


class NoArguments(BaseModel):
    pass


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("unknown_first", [False, True])
async def test_native_usage_is_persisted_and_replayed(streaming: bool, unknown_first: bool) -> None:
    url = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    tool_executions = 0

    def lookup() -> str:
        nonlocal tool_executions
        tool_executions += 1
        return "synthetic result"

    tools = ToolRegistry()
    tools.register(
        name="lookup",
        description="Return a synthetic result.",
        input_model=NoArguments,
        handler=lookup,
    )
    call = ToolCall(id="call-1", name="lookup", arguments={})
    model = DeterministicModelClient(
        [
            ModelResult(
                message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
                tool_calls=[call],
                usage=None if unknown_first else Usage(input_tokens=10, output_tokens=3),
            ),
            ModelResult(
                message=Message(role="assistant", content=[TextContent(text="Done")]),
                usage=Usage(input_tokens=20, output_tokens=5),
            ),
        ]
    )
    registry = AgentRegistry()
    registry.register(
        NativeAgentInvoker(agent_id="usage-test", version="1", model_client=model, tools=tools)
    )
    runtime = AgentRuntime(agents=registry, memory=memory, runs=runs, namespace=str(uuid4()))
    request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="Lookup")])])
    key = str(uuid4())
    expected = None if unknown_first else Usage(input_tokens=30, output_tokens=8)
    try:
        if streaming:
            events = [
                event async for event in runtime.stream("usage-test", request, idempotency_key=key)
            ]
            completed = events[-1]
            assert completed.type == "completed"
            response = completed.response
        else:
            response = await runtime.invoke("usage-test", request, idempotency_key=key)
        assert response.usage == expected
        record = await runs.get_by_idempotency(agent_id="usage-test", idempotency_key=key)
        assert record is not None and record.status == "succeeded"
        assert record.response == response
        assert len(await memory.get_messages(response.thread_id)) == 4

        replay = await runtime.invoke("usage-test", request, idempotency_key=key)
        replay_events = [
            event async for event in runtime.stream("usage-test", request, idempotency_key=key)
        ]
        replay_completed = replay_events[-1]
        assert replay_completed.type == "completed"
        assert replay_completed.response == replay == response
        assert len(model.calls) == 2
        assert tool_executions == 1
    finally:
        assert await runtime.shutdown()
        await engine.dispose()
