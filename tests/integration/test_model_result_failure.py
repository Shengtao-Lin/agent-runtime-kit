"""Malformed model/tool results fail durably without replaying external effects."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, RuntimeRequest
from agent_runtime.adapters import NativeAgentInvoker
from agent_runtime.errors import IdempotentRunUnavailableError, ProviderError, ToolExecutionError
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import Message, TextContent, ToolCall, ToolCallContent
from agent_runtime.models_clients import DeterministicModelClient, ModelResult
from agent_runtime.runs import PostgresRunStore
from agent_runtime.tools import ToolRegistry

pytestmark = pytest.mark.integration


class NoArguments(BaseModel):
    pass


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("failure", ["mismatch", "duplicate", "role", "tool-output"])
async def test_invalid_model_or_tool_result_never_commits_success(
    failure: str,
    streaming: bool,
) -> None:
    url = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    executions: list[str] = []

    def lookup() -> object:
        executions.append("external-effect")
        return {"PRIVATE-SENTINEL": float("nan")}

    tools = ToolRegistry()
    tools.register(
        name="lookup", description="Synthetic tool", input_model=NoArguments, handler=lookup
    )
    call = ToolCall(id="call-1", name="lookup", arguments={})
    result = ModelResult(
        message=Message(role="assistant", content=[ToolCallContent(tool_call=call)]),
        tool_calls=[call],
    )
    if failure == "mismatch":
        result.tool_calls.clear()
    elif failure == "duplicate":
        result.message.content.append(ToolCallContent(tool_call=call))
        result.tool_calls.append(call)
    elif failure == "role":
        result.message = Message(role="user", content=[TextContent(text="PRIVATE-SENTINEL")])
        result.tool_calls.clear()
    model = DeterministicModelClient([result])
    registry = AgentRegistry()
    registry.register(
        NativeAgentInvoker(agent_id="result-failure", version="1", model_client=model, tools=tools)
    )
    runtime = AgentRuntime(agents=registry, memory=memory, runs=runs, namespace=str(uuid4()))
    request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="lookup")])])
    key = str(uuid4())
    expected_error = ToolExecutionError if failure == "tool-output" else ProviderError
    observed: list[str] = []
    try:
        with pytest.raises(expected_error) as caught:
            if streaming:
                async for event in runtime.stream("result-failure", request, idempotency_key=key):
                    observed.append(event.type)
            else:
                await runtime.invoke("result-failure", request, idempotency_key=key)
        assert "PRIVATE-SENTINEL" not in caught.value.public_message
        assert caught.value.retryable is False
        assert "completed" not in observed and "tool_result" not in observed
        record = await runs.get_by_idempotency(agent_id="result-failure", idempotency_key=key)
        assert record is not None and record.status == "failed"
        assert record.error_code == expected_error.code
        assert record.response is None
        assert await memory.get_messages(record.thread_id) == ()
        with pytest.raises(IdempotentRunUnavailableError):
            await runtime.invoke("result-failure", request, idempotency_key=key)
        assert len(model.calls) == 1
        assert executions == (["external-effect"] if failure == "tool-output" else [])
    finally:
        assert await runtime.shutdown()
        await engine.dispose()
