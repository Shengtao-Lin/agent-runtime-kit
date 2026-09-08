from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime
from agent_runtime.adapters.base import AgentStreamEvent
from agent_runtime.hooks import (
    GuardrailBlockedError,
    GuardrailHook,
    HookManager,
    PhraseBlockGuardrail,
)
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    Message,
    RuntimeRequest,
    TextContent,
)
from agent_runtime.runs import PostgresRunStore

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if value is None:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    return value


@dataclass
class HistoryCountingInvoker:
    descriptor: AgentDescriptor
    call_count: int = 0
    last_message_count: int = 0

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        self.call_count += 1
        self.last_message_count = len(request.messages)
        return InvocationOutput(
            messages=[
                Message(
                    role="assistant",
                    content=[TextContent(text=f"Saw {len(request.messages)} messages")],
                )
            ]
        )


@dataclass
class BlockingInvoker:
    descriptor: AgentDescriptor
    started: asyncio.Event

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        del request
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def stream(self, request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        del request
        self.started.set()
        await asyncio.Event().wait()
        if False:
            yield


@pytest.mark.asyncio
async def test_runtime_persists_history_and_replays_idempotent_response() -> None:
    url = database_url()
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    invoker = HistoryCountingInvoker(
        AgentDescriptor(agent_id="runtime-agent", version="1", framework="native")
    )
    registry = AgentRegistry()
    registry.register(invoker)
    runtime = AgentRuntime(
        agents=registry,
        memory=memory,
        runs=runs,
        namespace=f"runtime-{uuid4()}",
    )
    try:
        first_request = RuntimeRequest(
            messages=[Message(role="user", content=[TextContent(text="First")])]
        )
        key = f"runtime-idempotency-{uuid4()}"
        first = await runtime.invoke("runtime-agent", first_request, idempotency_key=key)
        replay_request = RuntimeRequest(
            messages=[Message(role="user", content=[TextContent(text="First")])]
        )
        replay = await runtime.invoke("runtime-agent", replay_request, idempotency_key=key)
        assert replay == first
        assert invoker.call_count == 1

        second = await runtime.invoke(
            "runtime-agent",
            RuntimeRequest(
                thread_id=first.thread_id,
                messages=[Message(role="user", content=[TextContent(text="Second")])],
            ),
        )
        assert second.thread_id == first.thread_id
        assert invoker.last_message_count == 3
        stored = await memory.get_messages(first.thread_id)
        assert len(stored) == 4

        stream_request = RuntimeRequest(
            messages=[Message(role="user", content=[TextContent(text="Stream")])]
        )
        events = [event async for event in runtime.stream("runtime-agent", stream_request)]
        assert [event.type for event in events] == ["started", "completed"]
        completed = events[-1]
        assert completed.type == "completed"
        assert completed.response.message.role == "assistant"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_marks_guardrail_blocks_without_invoking_agent() -> None:
    url = database_url()
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    invoker = HistoryCountingInvoker(
        AgentDescriptor(agent_id="guarded-agent", version="1", framework="native")
    )
    registry = AgentRegistry()
    registry.register(invoker)
    hooks = HookManager(
        [
            GuardrailHook(
                PhraseBlockGuardrail(["blocked phrase"]),
                phases={"before_model"},
            )
        ]
    )
    runtime = AgentRuntime(
        agents=registry,
        memory=memory,
        runs=runs,
        hooks=hooks,
        namespace=f"guardrails-{uuid4()}",
    )
    key = f"blocked-{uuid4()}"
    request = RuntimeRequest(
        messages=[Message(role="user", content=[TextContent(text="A blocked phrase")])]
    )
    try:
        with pytest.raises(GuardrailBlockedError):
            await runtime.invoke("guarded-agent", request, idempotency_key=key)
        assert invoker.call_count == 0
        record = await runs.get_by_idempotency(agent_id="guarded-agent", idempotency_key=key)
        assert record is not None
        assert record.status == "blocked"
        assert await memory.get_messages(record.thread_id) == ()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_invocation_does_not_leave_running_record() -> None:
    url = database_url()
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    started = asyncio.Event()
    invoker = BlockingInvoker(
        AgentDescriptor(agent_id="blocking-agent", version="1", framework="native"),
        started,
    )
    registry = AgentRegistry()
    registry.register(invoker)
    runtime = AgentRuntime(
        agents=registry,
        memory=memory,
        runs=runs,
        namespace=f"cancellation-{uuid4()}",
    )
    key = f"cancelled-{uuid4()}"
    task = asyncio.create_task(
        runtime.invoke(
            "blocking-agent",
            RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="Wait")])]),
            idempotency_key=key,
        )
    )
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = await runs.get_by_idempotency(agent_id="blocking-agent", idempotency_key=key)
        assert record is not None
        assert record.status == "failed"
        assert record.error_code == "invocation_cancelled"
    finally:
        if not task.done():
            task.cancel()
        await engine.dispose()


@pytest.mark.asyncio
async def test_closed_stream_does_not_leave_running_record() -> None:
    url = database_url()
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    started = asyncio.Event()
    invoker = BlockingInvoker(
        AgentDescriptor(agent_id="stream-blocking-agent", version="1", framework="native"),
        started,
    )
    registry = AgentRegistry()
    registry.register(invoker)
    runtime = AgentRuntime(
        agents=registry,
        memory=memory,
        runs=runs,
        namespace=f"stream-cancellation-{uuid4()}",
    )
    key = f"stream-cancelled-{uuid4()}"
    stream = runtime.stream(
        "stream-blocking-agent",
        RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="Wait")])]),
        idempotency_key=key,
    )
    try:
        first = await anext(stream)
        assert first.type == "started"
        await started.wait()
        await stream.aclose()
        record = await runs.get_by_idempotency(
            agent_id="stream-blocking-agent", idempotency_key=key
        )
        assert record is not None
        assert record.status == "failed"
        assert record.error_code == "invocation_cancelled"
    finally:
        await stream.aclose()
        await engine.dispose()
