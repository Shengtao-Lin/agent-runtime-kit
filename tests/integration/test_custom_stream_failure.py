"""Invalid custom terminal streams cannot become durable success or replay work."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, RuntimeRequest
from agent_runtime.adapters.base import AgentCompleted, AgentStreamEvent, AgentTextDelta
from agent_runtime.errors import AgentInvocationError, IdempotentRunUnavailableError
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    Message,
    TextContent,
)
from agent_runtime.runs import PostgresRunStore

pytestmark = pytest.mark.integration


class CustomInvoker:
    descriptor = AgentDescriptor(agent_id="custom-stream", version="1", framework="native")

    def __init__(self, shape: str) -> None:
        self.shape = shape
        self.calls = 0
        self.closed = False

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        del request
        raise AssertionError("Invalid failed keys must not execute again")

    async def stream(self, request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        del request
        self.calls += 1
        try:
            yield AgentTextDelta(delta="provisional")
            if self.shape == "missing":
                return
            complete = AgentCompleted(
                output=InvocationOutput(
                    messages=[Message(role="assistant", content=[TextContent(text="done")])]
                )
            )
            yield complete
            if self.shape == "duplicate":
                yield complete
            else:
                yield AgentTextDelta(delta="PRIVATE-SENTINEL")
        finally:
            self.closed = True


@pytest.mark.parametrize("shape", ["missing", "duplicate", "late_text"])
async def test_invalid_custom_stream_persists_failure_and_never_reexecutes(shape: str) -> None:
    url = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if url is None:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    invoker = CustomInvoker(shape)
    registry = AgentRegistry()
    registry.register(invoker)
    runtime = AgentRuntime(agents=registry, memory=memory, runs=runs, namespace=str(uuid4()))
    key = str(uuid4())
    request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="hello")])])
    observed: list[str] = []
    try:
        with pytest.raises(AgentInvocationError) as caught:
            async for event in runtime.stream("custom-stream", request, idempotency_key=key):
                observed.append(event.type)
        assert "PRIVATE-SENTINEL" not in caught.value.public_message
        assert "completed" not in observed
        record = await runs.get_by_idempotency(agent_id="custom-stream", idempotency_key=key)
        assert record is not None and record.status == "failed"
        assert record.error_code == "agent_invocation_failed"
        assert record.response is None
        assert await memory.get_messages(record.thread_id) == ()
        with pytest.raises(IdempotentRunUnavailableError):
            await runtime.invoke("custom-stream", request, idempotency_key=key)
        assert invoker.calls == 1 and invoker.closed
    finally:
        assert await runtime.shutdown()
        await engine.dispose()
