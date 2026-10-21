"""Deterministic deadline coverage without a database or external provider."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent_runtime import AgentRegistry, AgentRuntime
from agent_runtime.adapters.base import AgentCompleted, AgentStreamEvent, AgentTextDelta
from agent_runtime.errors import InvocationTimeoutError
from agent_runtime.hooks import HookEvent, HookManager
from agent_runtime.memory.base import MemoryStore
from agent_runtime.memory.models import ThreadRecord
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    Message,
    RuntimeRequest,
    RuntimeResponse,
    TextContent,
)
from agent_runtime.runs.base import RunStore
from agent_runtime.runs.models import RunClaim, RunRecord


class Invoker:
    descriptor = AgentDescriptor(agent_id="deadline", version="1", framework="native")

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        del request
        return InvocationOutput(
            messages=[Message(role="assistant", content=[TextContent(text="result")])]
        )

    async def stream(self, request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        yield AgentCompleted(output=await self.invoke(request))


def harness() -> SimpleNamespace:
    now = datetime.now(UTC)
    thread = ThreadRecord(id=uuid4(), namespace="deadline", created_at=now, updated_at=now)
    record = RunRecord(
        id=uuid4(),
        request_id=uuid4(),
        agent=Invoker.descriptor,
        thread_id=thread.id,
        status="running",
        started_at=now,
    )
    memory = SimpleNamespace(
        create_thread=AsyncMock(return_value=thread),
        get_thread=AsyncMock(return_value=thread),
        get_messages=AsyncMock(return_value=()),
    )
    runs = SimpleNamespace(
        recover=AsyncMock(return_value=record),
        get_by_idempotency=AsyncMock(return_value=None),
        claim=AsyncMock(return_value=RunClaim(run=record, created=True)),
        transition=AsyncMock(return_value=record),
        complete=AsyncMock(return_value=record),
        fail_if_running=AsyncMock(return_value=record.model_copy(update={"status": "failed"})),
    )
    agents = AgentRegistry()
    invoker = Invoker()
    agents.register(invoker)
    hooks = HookManager()
    runtime = AgentRuntime(
        agents=agents,
        memory=cast(MemoryStore, memory),
        runs=cast(RunStore, runs),
        hooks=hooks,
        invocation_timeout_seconds=0.05,
        cleanup_timeout_seconds=0.05,
    )
    return SimpleNamespace(
        runtime=runtime,
        memory=memory,
        runs=runs,
        hooks=hooks,
        invoker=invoker,
        record=record,
        request=RuntimeRequest(
            thread_id=thread.id,
            messages=[Message(role="user", content=[TextContent(text="hello")])],
        ),
    )


async def call(h: SimpleNamespace, mode: str) -> RuntimeResponse:
    if mode == "invoke":
        return await h.runtime.invoke("deadline", h.request, idempotency_key="key")
    events = [
        event async for event in h.runtime.stream("deadline", h.request, idempotency_key="key")
    ]
    assert events[-1].type == "completed"
    return events[-1].response


async def stall(*args: object, **kwargs: object) -> None:
    del args, kwargs
    await asyncio.Event().wait()


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize(
    "phase",
    [
        "lookup",
        "recover",
        "create",
        "get_thread",
        "claim",
        "transition",
        "history",
        "before",
        "model",
        "after",
        "complete",
    ],
)
@pytest.mark.asyncio
async def test_every_phase_obeys_one_deadline(
    mode: str, phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness()
    points = {
        "recover": h.runs.recover,
        "lookup": h.runs.get_by_idempotency,
        "create": h.memory.create_thread,
        "get_thread": h.memory.get_thread,
        "claim": h.runs.claim,
        "transition": h.runs.transition,
        "history": h.memory.get_messages,
        "complete": h.runs.complete,
    }
    if phase == "create":
        h.request = h.request.model_copy(update={"thread_id": None})
    if phase == "recover":
        h.runs.get_by_idempotency.return_value = h.record.model_copy(
            update={"request_hash": AgentRuntime.request_hash(h.request)}
        )
    if phase in points:
        points[phase].side_effect = stall
    elif phase == "model":
        monkeypatch.setattr(h.invoker, "invoke", stall)
    else:

        async def slow_hook(event: HookEvent) -> None:
            if event.phase == f"{phase}_model":
                await stall()

        monkeypatch.setattr(h.hooks, "run", slow_hook)
    async with asyncio.timeout(2):
        with pytest.raises(InvocationTimeoutError):
            await call(h, mode)
    if phase in {"lookup", "recover", "create", "get_thread"}:
        h.runs.fail_if_running.assert_not_awaited()
    else:
        assert h.runs.fail_if_running.await_args.kwargs["error_code"] == "invocation_timeout"
    if phase != "complete":
        h.runs.complete.assert_not_awaited()


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.asyncio
async def test_budget_is_shared_across_phases(mode: str) -> None:
    h = harness()

    async def delayed(value: object) -> object:
        await asyncio.sleep(0.035)
        return value

    async def get_thread(*args: object) -> object:
        return await delayed(h.memory.get_thread.return_value)

    async def history(*args: object) -> object:
        return await delayed(())

    h.memory.get_thread.side_effect = get_thread
    h.memory.get_messages.side_effect = history
    with pytest.raises(InvocationTimeoutError):
        await call(h, mode)
    h.runs.complete.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("original"), asyncio.CancelledError()])
@pytest.mark.asyncio
async def test_cleanup_has_separate_budget_and_preserves_original(
    error: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    h = harness()
    h.runs.complete.side_effect = error
    h.runs.fail_if_running.side_effect = stall
    async with asyncio.timeout(2):
        with pytest.raises(type(error)):
            await call(h, "invoke")
    assert "run_cleanup_unavailable" in caplog.text


@pytest.mark.asyncio
async def test_timeout_after_commit_returns_durable_response() -> None:
    h = harness()
    response = RuntimeResponse(
        run_id=h.record.id,
        request_id=h.request.request_id,
        thread_id=h.request.thread_id,
        agent=Invoker.descriptor,
        message=Message(role="assistant", content=[TextContent(text="saved")]),
    )
    h.runs.complete.side_effect = stall
    h.runs.fail_if_running.return_value = h.record.model_copy(
        update={
            "status": "succeeded",
            "response": response,
        }
    )
    assert await call(h, "invoke") == response


@pytest.mark.asyncio
async def test_dependency_timeout_is_not_reclassified_as_runtime_deadline() -> None:
    h = harness()
    h.runs.complete.side_effect = TimeoutError("dependency timeout")
    with pytest.raises(TimeoutError, match="dependency timeout"):
        await call(h, "invoke")
    assert h.runs.fail_if_running.await_args.kwargs["error_code"] == "internal_error"


@pytest.mark.asyncio
async def test_close_full_stream_queue_does_not_deadlock(monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness()
    full = asyncio.Event()
    closed = asyncio.Event()

    async def flood(request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        del request
        try:
            for index in range(100):
                if index == 64:
                    full.set()
                yield AgentTextDelta(delta="x")
        finally:
            closed.set()

    monkeypatch.setattr(h.invoker, "stream", flood)
    stream = h.runtime.stream("deadline", h.request)
    assert (await anext(stream)).type == "started"
    await asyncio.wait_for(full.wait(), timeout=2)
    await asyncio.wait_for(stream.aclose(), timeout=2)
    await asyncio.wait_for(closed.wait(), timeout=2)
    h.runs.complete.assert_not_awaited()


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
@pytest.mark.parametrize("option", ["invocation_timeout_seconds", "cleanup_timeout_seconds"])
def test_timeout_configuration_is_finite_and_positive(option: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        AgentRuntime(
            agents=AgentRegistry(),
            memory=cast(MemoryStore, None),
            runs=cast(RunStore, None),
            invocation_timeout_seconds=value if option == "invocation_timeout_seconds" else 60,
            cleanup_timeout_seconds=value if option == "cleanup_timeout_seconds" else 5,
        )
