"""Real PostgreSQL fault injection around the successful commit boundary."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Connection, event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapper

from agent_runtime import AgentRegistry, AgentRuntime
from agent_runtime.errors import (
    IdempotentRunUnavailableError,
    InvalidRunTransitionError,
    InvocationTimeoutError,
)
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.memory.tables import AgentMessageTable, RuntimeRunTable
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    Message,
    RuntimeRequest,
    RuntimeResponse,
    TextContent,
)
from agent_runtime.runs import PostgresRunStore, RunRecord
from agent_runtime.telemetry import RuntimeTelemetry

pytestmark = pytest.mark.integration


@dataclass
class CountingInvoker:
    descriptor: AgentDescriptor
    calls: int = 0

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        self.calls += 1
        return InvocationOutput(
            messages=[
                Message(
                    role="assistant",
                    content=[TextContent(text=f"Saw {len(request.messages)} messages")],
                )
            ]
        )


@dataclass
class Harness:
    engine: AsyncEngine
    memory: PostgresMemoryStore
    runs: PostgresRunStore
    runtime: AgentRuntime
    invoker: CountingInvoker
    request: RuntimeRequest
    key: str

    @property
    def agent_id(self) -> str:
        return self.invoker.descriptor.agent_id


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[Harness]:
    url = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    runs = PostgresRunStore(engine)
    identity = f"atomic-{uuid4()}"
    invoker = CountingInvoker(AgentDescriptor(agent_id=identity, version="1", framework="native"))
    agents = AgentRegistry()
    agents.register(invoker)
    runtime = AgentRuntime(agents=agents, memory=memory, runs=runs, namespace=identity)
    try:
        thread = await memory.create_thread(namespace=identity)
        request = RuntimeRequest(
            thread_id=thread.id,
            messages=[Message(role="user", content=[TextContent(text="Synthetic input")])],
        )
        yield Harness(engine, memory, runs, runtime, invoker, request, f"key-{identity}")
    finally:
        await engine.dispose()


async def invoke(harness: Harness, mode: str) -> RuntimeResponse:
    if mode == "invoke":
        return await harness.runtime.invoke(
            harness.agent_id, harness.request, idempotency_key=harness.key
        )
    events = [
        event
        async for event in harness.runtime.stream(
            harness.agent_id, harness.request, idempotency_key=harness.key
        )
    ]
    assert events[-1].type == "completed"
    return events[-1].response


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.asyncio
async def test_failure_after_message_flush_rolls_back_everything(
    harness: Harness, mode: str
) -> None:
    def fail(mapper: Mapper[RuntimeRunTable], connection: Connection, row: RuntimeRunTable) -> None:
        del mapper
        if row.agent_id == harness.agent_id and row.status == "succeeded":
            count = connection.scalar(
                select(func.count())
                .select_from(AgentMessageTable)
                .where(AgentMessageTable.thread_id == row.thread_id)
            )
            assert count == 2  # Messages reached PostgreSQL inside the uncommitted transaction.
            raise RuntimeError("Synthetic finalization failure")

    event.listen(RuntimeRunTable, "before_update", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic finalization failure"):
            await invoke(harness, mode)
    finally:
        event.remove(RuntimeRunTable, "before_update", fail)
    record = await harness.runs.get_by_idempotency(
        agent_id=harness.agent_id, idempotency_key=harness.key
    )
    assert record is not None and record.status == "failed" and record.response is None
    assert await harness.memory.get_messages(record.thread_id) == ()
    with pytest.raises(IdempotentRunUnavailableError):
        await invoke(harness, mode)
    assert harness.invoker.calls == 1
    await harness.runtime.invoke(
        harness.agent_id,
        RuntimeRequest(
            thread_id=record.thread_id,
            messages=[Message(role="user", content=[TextContent(text="Synthetic input")])],
        ),
        idempotency_key=f"retry-{harness.key}",
    )
    assert [item.sequence for item in await harness.memory.get_messages(record.thread_id)] == [1, 2]


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.asyncio
async def test_lost_commit_acknowledgment_preserves_success(
    harness: Harness, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    complete = harness.runs.complete

    async def lose_ack(
        run_id: UUID, *, owner_token: UUID, messages: Sequence[Message], response: RuntimeResponse
    ) -> RunRecord:
        await complete(run_id, owner_token=owner_token, messages=messages, response=response)
        raise ConnectionError("Synthetic lost commit acknowledgment")

    monkeypatch.setattr(harness.runs, "complete", lose_ack)
    first = await invoke(harness, mode)
    replay = await invoke(harness, mode)
    assert replay == first and harness.invoker.calls == 1
    record = await harness.runs.get(first.run_id)
    assert record is not None and record.status == "succeeded" and record.response == first
    assert record.error_code is None
    assert len(await harness.memory.get_messages(first.thread_id)) == 2


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.asyncio
async def test_cancellation_during_commit_rolls_back_messages(
    harness: Harness, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed = asyncio.Event()

    class PausingSession(AsyncSession):
        async def flush(self, objects: Sequence[Any] | None = None) -> None:
            writing_messages = any(isinstance(row, AgentMessageTable) for row in self.new)
            await super().flush(objects)
            if writing_messages:
                flushed.set()
                await asyncio.Event().wait()

    monkeypatch.setattr(
        harness.runs,
        "_sessions",
        async_sessionmaker(harness.engine, class_=PausingSession, expire_on_commit=False),
    )
    stream = harness.runtime.stream(harness.agent_id, harness.request, idempotency_key=harness.key)
    task = None
    try:
        if mode == "invoke":
            task = asyncio.create_task(invoke(harness, mode))
        else:
            assert (await anext(stream)).type == "started"
        await asyncio.wait_for(flushed.wait(), timeout=5)
        assert harness.request.thread_id is not None
        assert await harness.memory.get_messages(harness.request.thread_id) == ()
        if task is not None:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await stream.aclose()
        record = await harness.runs.get_by_idempotency(
            agent_id=harness.agent_id, idempotency_key=harness.key
        )
        assert record is not None and record.status == "failed"
        assert record.error_code == "invocation_cancelled" and record.response is None
        assert await harness.memory.get_messages(record.thread_id) == ()
    finally:
        await stream.aclose()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancellation_after_commit_preserves_replay(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = asyncio.Event()
    complete = harness.runs.complete

    async def wait_after_commit(
        run_id: UUID, *, owner_token: UUID, messages: Sequence[Message], response: RuntimeResponse
    ) -> RunRecord:
        result = await complete(
            run_id, owner_token=owner_token, messages=messages, response=response
        )
        committed.set()
        await asyncio.Event().wait()
        return result

    monkeypatch.setattr(harness.runs, "complete", wait_after_commit)
    task = asyncio.create_task(invoke(harness, "invoke"))
    try:
        await asyncio.wait_for(committed.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        record = await harness.runs.get_by_idempotency(
            agent_id=harness.agent_id, idempotency_key=harness.key
        )
        assert record is not None and record.status == "succeeded"
        assert record.response is not None and record.error_code is None
        replay = await invoke(harness, "invoke")
        assert replay == record.response and harness.invoker.calls == 1
        assert len(await harness.memory.get_messages(record.thread_id)) == 2
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cleanup_outage_does_not_replace_original_error(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def fail_complete(
        run_id: UUID, *, owner_token: UUID, messages: Sequence[Message], response: RuntimeResponse
    ) -> RunRecord:
        del run_id, owner_token, messages, response
        raise RuntimeError("original-sentinel")

    async def fail_cleanup(*args: object, **kwargs: object) -> RunRecord:
        del args, kwargs
        raise RuntimeError("cleanup-secret-sentinel")

    monkeypatch.setattr(harness.runs, "complete", fail_complete)
    monkeypatch.setattr(harness.runs, "fail_if_running", fail_cleanup)
    with pytest.raises(RuntimeError, match="original-sentinel"):
        await invoke(harness, "invoke")
    assert "run_cleanup_unavailable" in caplog.text
    assert "cleanup-secret-sentinel" not in caplog.text
    record = await harness.runs.get_by_idempotency(
        agent_id=harness.agent_id, idempotency_key=harness.key
    )
    assert record is not None and record.status == "running"
    assert await harness.memory.get_messages(record.thread_id) == ()


@pytest.mark.parametrize("field", ["run_id", "request_id", "thread_id", "message", "agent"])
@pytest.mark.asyncio
async def test_complete_rejects_foreign_response_without_writes(
    harness: Harness, field: str
) -> None:
    run_id = uuid4()
    owner_token = uuid4()
    assert harness.request.thread_id is not None
    await harness.runs.claim(
        run_id=run_id,
        request_id=harness.request.request_id,
        agent=harness.invoker.descriptor,
        thread_id=harness.request.thread_id,
        idempotency_key=harness.key,
        request_hash="hash",
        owner_token=owner_token,
        lease_seconds=60,
    )
    await harness.runs.transition(run_id, "running", owner_token=owner_token)
    assistant = Message(role="assistant", content=[TextContent(text="Synthetic result")])
    response = RuntimeResponse(
        run_id=run_id,
        request_id=harness.request.request_id,
        thread_id=harness.request.thread_id,
        agent=harness.invoker.descriptor,
        message=assistant,
    )
    replacements: dict[str, object] = {
        "run_id": uuid4(),
        "request_id": uuid4(),
        "thread_id": uuid4(),
        "message": Message(role="assistant", content=[TextContent(text="Different result")]),
        "agent": harness.invoker.descriptor.model_copy(update={"agent_id": "foreign-agent"}),
    }
    invalid = response.model_copy(update={field: replacements[field]})
    with pytest.raises(InvalidRunTransitionError):
        await harness.runs.complete(
            run_id,
            owner_token=owner_token,
            messages=[*harness.request.messages, assistant],
            response=invalid,
        )
    record = await harness.runs.get(run_id)
    assert record is not None and record.status == "running" and record.response is None
    assert await harness.memory.get_messages(record.thread_id) == ()


@pytest.mark.asyncio
async def test_terminal_cleanup_and_repeated_completion_cannot_rewrite_success(
    harness: Harness,
) -> None:
    response = await invoke(harness, "invoke")
    record = await harness.runs.get(response.run_id)
    assert record is not None and record.owner_token is not None
    owner_token = record.owner_token
    preserved = await harness.runs.fail_if_running(
        response.run_id, owner_token=owner_token, error_code="late_failure"
    )
    assert preserved.status == "succeeded" and preserved.response == response
    assert preserved.error_code is None
    with pytest.raises(InvalidRunTransitionError):
        await harness.runs.complete(
            response.run_id, owner_token=owner_token, messages=[response.message], response=response
        )
    with pytest.raises(InvalidRunTransitionError, match="complete"):
        await harness.runs.transition(
            response.run_id, "succeeded", owner_token=owner_token, response=response
        )
    assert len(await harness.memory.get_messages(response.thread_id)) == 2


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.asyncio
async def test_deadline_at_commit_boundary(
    harness: Harness, mode: str, committed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = AgentRegistry()
    agents.register(harness.invoker)
    harness.runtime = AgentRuntime(
        agents=agents,
        memory=harness.memory,
        runs=harness.runs,
        invocation_timeout_seconds=0.5,
        cleanup_timeout_seconds=0.5,
        namespace=harness.invoker.descriptor.agent_id,
    )
    reached = False
    complete = harness.runs.complete

    async def pause(
        run_id: UUID, *, owner_token: UUID, messages: Sequence[Message], response: RuntimeResponse
    ) -> RunRecord:
        nonlocal reached
        if committed:
            await complete(run_id, owner_token=owner_token, messages=messages, response=response)
        reached = True
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(harness.runs, "complete", pause)
    if committed:
        response = await invoke(harness, mode)
        assert await invoke(harness, mode) == response
    else:
        with pytest.raises(InvocationTimeoutError):
            await invoke(harness, mode)
    assert reached
    record = await harness.runs.get_by_idempotency(
        agent_id=harness.agent_id, idempotency_key=harness.key
    )
    assert record is not None
    assert record.status == ("succeeded" if committed else "failed")
    assert len(await harness.memory.get_messages(record.thread_id)) == (2 if committed else 0)
    assert harness.invoker.calls == 1


@pytest.mark.asyncio
async def test_accepted_run_can_be_cleaned_after_lost_claim_ack(harness: Harness) -> None:
    run_id = uuid4()
    owner_token = uuid4()
    assert harness.request.thread_id is not None
    await harness.runs.claim(
        run_id=run_id,
        request_id=harness.request.request_id,
        agent=harness.invoker.descriptor,
        thread_id=harness.request.thread_id,
        idempotency_key=harness.key,
        request_hash="hash",
        owner_token=owner_token,
        lease_seconds=60,
    )
    result = await harness.runs.fail_if_running(
        run_id, owner_token=owner_token, error_code="invocation_timeout"
    )
    assert result.status == "failed" and result.error_code == "invocation_timeout"


@pytest.mark.asyncio
async def test_runtime_spans_and_logs_share_safe_correlated_ids(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    agents = AgentRegistry()
    agents.register(harness.invoker)
    harness.runtime = AgentRuntime(
        agents=agents,
        memory=harness.memory,
        runs=harness.runs,
        namespace=harness.agent_id,
        telemetry=RuntimeTelemetry(provider.get_tracer("test")),
    )
    harness.request = harness.request.model_copy(
        update={
            "metadata": {
                "trace_id": "spoofed-id",
                "secret": "private-metadata-sentinel",
            }
        }
    )
    caplog.set_level(logging.INFO, logger="agent_runtime.runtime")
    try:
        response = await invoke(harness, "invoke")
        span = next(s for s in exporter.get_finished_spans() if s.name == "agent.runtime.run")
        assert span.attributes is not None
        assert span.attributes["agent.run.id"] == str(response.run_id)
        assert span.attributes["agent.thread.id"] == str(response.thread_id)
        assert span.attributes["agent.version"] == harness.invoker.descriptor.version
        assert span.attributes["agent.framework"] == "native"
        log = next(r for r in caplog.records if r.message == "agent_invocation_completed")
        assert log.__dict__["request_id"] == str(harness.request.request_id)
        assert log.__dict__["run_id"] == str(response.run_id)
        assert span.context is not None
        assert log.__dict__["trace_id"] == f"{span.context.trace_id:032x}"
        assert "private-metadata-sentinel" not in str(span.attributes)
        assert "spoofed-id" not in str(log.__dict__)
    finally:
        provider.shutdown()
