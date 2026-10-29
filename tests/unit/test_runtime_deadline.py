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
from agent_runtime.errors import (
    AgentInvocationError,
    InvocationTimeoutError,
    RuntimeClosingError,
    RuntimeShutdownTimeoutError,
)
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


@pytest.mark.parametrize("shape", ["missing", "duplicate", "late_text"])
async def test_custom_invoker_terminal_contract_prevents_completion(
    shape: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness(timeout=2)
    closed = asyncio.Event()

    async def malformed(request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        try:
            yield AgentTextDelta(delta="provisional")
            if shape == "missing":
                return
            result = AgentCompleted(output=await h.invoker.invoke(request))
            yield result
            if shape == "duplicate":
                yield result
            else:
                yield AgentTextDelta(delta="PRIVATE-SENTINEL")
        finally:
            closed.set()

    monkeypatch.setattr(h.invoker, "stream", malformed)
    seen: list[str] = []
    with pytest.raises(AgentInvocationError) as caught:
        async for event in h.runtime.stream("deadline", h.request):
            seen.append(event.type)
    assert "PRIVATE-SENTINEL" not in caught.value.public_message
    assert "completed" not in seen
    assert closed.is_set()
    h.runs.complete.assert_not_awaited()
    assert h.runs.fail_if_running.call_args.kwargs["error_code"] == "agent_invocation_failed"
    assert await h.runtime.shutdown(timeout_seconds=2)


async def test_completion_requires_source_eof_within_invocation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness(timeout=0.1)
    closed = asyncio.Event()

    async def never_ends(request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        try:
            yield AgentCompleted(output=await h.invoker.invoke(request))
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(h.invoker, "stream", never_ends)
    with pytest.raises(InvocationTimeoutError):
        await call(h, "stream")
    assert closed.is_set()
    h.runs.complete.assert_not_awaited()
    assert await h.runtime.shutdown(timeout_seconds=2)


class Invoker:
    descriptor = AgentDescriptor(agent_id="deadline", version="1", framework="native")

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        del request
        return InvocationOutput(
            messages=[Message(role="assistant", content=[TextContent(text="result")])]
        )

    async def stream(self, request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        yield AgentCompleted(output=await self.invoke(request))


def harness(timeout: float = 0.05) -> SimpleNamespace:
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
        invocation_timeout_seconds=timeout,
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

    async def get_thread(*args: object, **kwargs: object) -> object:
        return await delayed(h.memory.get_thread.return_value)

    async def history(*args: object, **kwargs: object) -> object:
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
@pytest.mark.parametrize("startup_delay", [0, 0.08])
async def test_close_full_stream_queue_does_not_deadlock(
    monkeypatch: pytest.MonkeyPatch, startup_delay: float
) -> None:
    # This tests backpressure cancellation, not the harness's 50 ms deadline.
    # Keep the invocation budget outside the explicit two-second deadlock guards.
    h = harness(timeout=10)
    full = asyncio.Event()
    closed = asyncio.Event()

    async def flood(request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        del request
        try:
            await asyncio.sleep(startup_delay)
            for index in range(100):
                if index == 64:
                    full.set()
                yield AgentTextDelta(delta="x")
        finally:
            closed.set()

    monkeypatch.setattr(h.invoker, "stream", flood)
    stream = h.runtime.stream("deadline", h.request)
    try:
        assert (await anext(stream)).type == "started"
        await asyncio.wait_for(full.wait(), timeout=2)
        await asyncio.wait_for(stream.aclose(), timeout=2)
        await asyncio.wait_for(closed.wait(), timeout=2)
        h.runs.complete.assert_not_awaited()
        assert h.runs.fail_if_running.await_args.kwargs["error_code"] == "invocation_cancelled"
    finally:
        await asyncio.wait_for(stream.aclose(), timeout=2)
        assert await h.runtime.shutdown(timeout_seconds=2)


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


@pytest.mark.asyncio
async def test_shutdown_cancels_active_work_and_rejects_new_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_runtime.errors import RuntimeClosingError

    h = harness(5)
    entered = asyncio.Event()

    async def blocking(*args: object, **kwargs: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(h.invoker, "invoke", blocking)
    task = asyncio.create_task(call(h, "invoke"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert await h.runtime.shutdown(timeout_seconds=1)
    with pytest.raises(asyncio.CancelledError):
        await task
    for mode in ("invoke", "stream"):
        with pytest.raises(RuntimeClosingError):
            await call(h, mode)
    h.runs.fail_if_running.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_waits_for_cleanup_after_caller_cancellation() -> None:
    h = harness(5)
    entered, release = asyncio.Event(), asyncio.Event()

    async def cleanup(*args: object, **kwargs: object) -> object:
        entered.set()
        await release.wait()
        return h.runs.fail_if_running.return_value

    h.runs.complete.side_effect = RuntimeError("original")
    h.runs.fail_if_running.side_effect = cleanup
    task = asyncio.create_task(call(h, "invoke"))
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await h.runtime.shutdown(timeout_seconds=0.001)
    release.set()
    assert await h.runtime.shutdown(timeout_seconds=1)
    h.runs.fail_if_running.assert_awaited_once()


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
@pytest.mark.asyncio
async def test_shutdown_requires_a_finite_positive_budget(budget: float) -> None:
    h = harness()
    with pytest.raises(ValueError, match="finite and positive"):
        await h.runtime.shutdown(timeout_seconds=budget)


async def test_lifespan_is_single_use_and_does_not_own_dependencies() -> None:
    h = harness()
    async with h.runtime.lifespan(shutdown_timeout_seconds=1) as entered:
        assert entered is h.runtime
        with pytest.raises(RuntimeError, match="already active"):
            async with h.runtime.lifespan():
                pytest.fail("Nested entry must fail")
    with pytest.raises(RuntimeClosingError):
        async with h.runtime.lifespan():
            pytest.fail("Closed runtimes must not reopen")
    h.memory.get_messages.assert_not_awaited()
    h.runs.complete.assert_not_awaited()


async def test_lifespan_preserves_body_error_and_calls_explicit_shutdown_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness()
    shutdown = AsyncMock(return_value=True)
    monkeypatch.setattr(h.runtime, "shutdown", shutdown)
    with pytest.raises(ValueError, match="application failure"):
        async with h.runtime.lifespan(shutdown_timeout_seconds=2):
            raise ValueError("application failure")
    shutdown.assert_awaited_once_with(timeout_seconds=2)


async def test_lifespan_surfaces_incomplete_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    h = harness()
    monkeypatch.setattr(h.runtime, "shutdown", AsyncMock(return_value=False))
    with pytest.raises(RuntimeShutdownTimeoutError, match="exceeded"):
        async with h.runtime.lifespan(shutdown_timeout_seconds=0.01):
            pass


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan")])
async def test_lifespan_validates_budget_before_entering(budget: float) -> None:
    h = harness()
    with pytest.raises(ValueError, match="finite and positive"):
        async with h.runtime.lifespan(shutdown_timeout_seconds=budget):
            pytest.fail("Invalid shutdown configuration must fail before serving")


async def test_lifespan_cancels_active_work_before_host_resources_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness(5)
    entered = asyncio.Event()

    async def blocking(*args: object, **kwargs: object) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(h.invoker, "invoke", blocking)
    async with h.runtime.lifespan(shutdown_timeout_seconds=1):
        task = asyncio.create_task(call(h, "invoke"))
        await asyncio.wait_for(entered.wait(), timeout=2)
    with pytest.raises(asyncio.CancelledError):
        await task
    h.runs.fail_if_running.assert_awaited_once()


async def test_lifespan_preserves_caller_cancellation() -> None:
    h = harness()
    entered = asyncio.Event()

    async def host() -> None:
        async with h.runtime.lifespan():
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(host())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(RuntimeClosingError):
        await call(h, "invoke")
