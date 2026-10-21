"""Real PostgreSQL arbitration, expiry, and cross-process ownership tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.errors import (
    IdempotentRunUnavailableError,
    InvalidRunTransitionError,
    RunOwnershipLostError,
    ThreadBusyError,
)
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.memory.tables import AgentMessageTable, AgentThreadTable
from agent_runtime.models import AgentDescriptor, InvocationInput, InvocationOutput, RuntimeResponse
from agent_runtime.runs import PostgresRunStore, RunRecord

pytestmark = pytest.mark.integration
WORKER = Path(__file__).resolve().parents[2] / "validation" / "consumer" / "lease_worker.py"


@dataclass
class Harness:
    engine: AsyncEngine
    memory: PostgresMemoryStore
    runs: PostgresRunStore
    thread_id: UUID
    agent: AgentDescriptor

    async def claim(self, *, lease: float = 60, key: str | None = None) -> RunRecord:
        result = await self.runs.claim(
            run_id=uuid4(),
            request_id=uuid4(),
            agent=self.agent,
            thread_id=self.thread_id,
            idempotency_key=key,
            request_hash="hash",
            owner_token=uuid4(),
            lease_seconds=lease,
        )
        return result.run


@pytest_asyncio.fixture
async def h() -> AsyncIterator[Harness]:
    url = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    memory = PostgresMemoryStore(engine)
    identity = f"ownership-{uuid4()}"
    thread = await memory.create_thread(namespace=identity)
    try:
        yield Harness(
            engine,
            memory,
            PostgresRunStore(engine),
            thread.id,
            AgentDescriptor(agent_id=identity, version="1", framework="native"),
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_stores_have_one_active_owner(h: Harness) -> None:
    # Separate engines/pools model distinct worker lifetimes.
    other_engine = create_async_engine(h.engine.url)
    other = PostgresRunStore(other_engine)
    try:

        async def claim(runs: PostgresRunStore) -> RunRecord:
            result = await runs.claim(
                run_id=uuid4(),
                request_id=uuid4(),
                agent=h.agent,
                thread_id=h.thread_id,
                idempotency_key=None,
                request_hash="hash",
                owner_token=uuid4(),
                lease_seconds=60,
            )
            return result.run

        results = await asyncio.gather(claim(h.runs), claim(other), return_exceptions=True)
        assert sum(isinstance(r, RunRecord) for r in results) == 1
        assert sum(isinstance(r, ThreadBusyError) for r in results) == 1
    finally:
        await other_engine.dispose()


@pytest.mark.parametrize("phase", ["accepted", "running"])
@pytest.mark.asyncio
async def test_expiry_releases_thread_and_fences_old_owner(h: Harness, phase: str) -> None:
    old = await h.claim(lease=0.15)
    assert old.owner_token is not None
    if phase == "running":
        await h.runs.transition(old.id, "running", owner_token=old.owner_token)
    await asyncio.sleep(0.2)
    new = await h.claim()
    recovered = await h.runs.get(old.id)
    assert recovered is not None and recovered.status == "failed"
    assert recovered.error_code == "owner_lease_expired"
    assistant = Message(role="assistant", content=[TextContent(text="stale")])
    response = RuntimeResponse(
        run_id=old.id,
        request_id=old.request_id,
        thread_id=h.thread_id,
        agent=h.agent,
        message=assistant,
    )
    with pytest.raises(InvalidRunTransitionError):
        await h.runs.complete(
            old.id, owner_token=old.owner_token, messages=[assistant], response=response
        )
    # Late cleanup cannot alter the new owner's row or release its thread.
    await h.runs.fail_if_running(old.id, owner_token=old.owner_token, error_code="late")
    with pytest.raises(ThreadBusyError):
        await h.claim()
    current = await h.runs.get(new.id)
    assert current is not None and current.status == "accepted"
    assert await h.memory.get_messages(h.thread_id) == ()


@pytest.mark.parametrize("operation", ["transition", "complete", "cleanup"])
@pytest.mark.asyncio
async def test_foreign_owner_cannot_mutate_run(h: Harness, operation: str) -> None:
    run = await h.claim()
    assert run.owner_token is not None
    await h.runs.transition(run.id, "running", owner_token=run.owner_token)
    with pytest.raises(RunOwnershipLostError):
        if operation == "transition":
            await h.runs.transition(run.id, "failed", owner_token=uuid4())
        elif operation == "cleanup":
            await h.runs.fail_if_running(run.id, owner_token=uuid4(), error_code="foreign")
        else:
            message = Message(role="assistant", content=[TextContent(text="foreign")])
            await h.runs.complete(
                run.id,
                owner_token=uuid4(),
                messages=[message],
                response=RuntimeResponse(
                    run_id=run.id,
                    request_id=run.request_id,
                    thread_id=h.thread_id,
                    agent=h.agent,
                    message=message,
                ),
            )
    record = await h.runs.get(run.id)
    assert record is not None and record.status == "running" and record.error_code is None
    assert "owner_token" not in record.model_dump()


@pytest.mark.asyncio
async def test_live_owner_is_not_recovered_and_expired_sweep_skips_locks(h: Harness) -> None:
    run = await h.claim(lease=0.15)
    assert (await h.runs.recover(run.id)).status == "accepted"
    await asyncio.sleep(0.2)
    async with h.engine.begin() as connection:
        await connection.execute(
            select(AgentThreadTable).where(AgentThreadTable.id == h.thread_id).with_for_update()
        )
        async with asyncio.timeout(2):
            swept = await h.runs.recover_expired(limit=1000)
        assert run.id not in {item.id for item in swept}
    swept = await h.runs.recover_expired(limit=1000)
    assert run.id in {item.id for item in swept}
    assert (await h.runs.recover(run.id)).error_code == "owner_lease_expired"


@pytest.mark.parametrize("killed", [False, True])
@pytest.mark.asyncio
async def test_independent_worker_overlap_and_crash(h: Harness, killed: bool) -> None:
    process = await asyncio.to_thread(
        subprocess.Popen,
        [
            sys.executable,
            "-u",
            str(WORKER),
            h.agent.agent_id,
            str(h.thread_id),
            "0.5" if killed else "30",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=10)
        assert line.strip() == "RUNNING"
        if killed:
            process.kill()
            await asyncio.to_thread(process.wait, 5)
            await asyncio.sleep(0.6)
            run = await h.runs.get_by_idempotency(
                agent_id=h.agent.agent_id, idempotency_key="lease-worker"
            )
            assert run is not None
            assert (await h.runs.recover(run.id)).error_code == "owner_lease_expired"
            await h.claim()
        else:
            with pytest.raises(ThreadBusyError):
                await h.claim()
        assert await h.memory.get_messages(h.thread_id) == ()
    finally:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.communicate, timeout=5)


@pytest.mark.asyncio
async def test_runtime_retries_recover_stale_key_without_invoking_tools(h: Harness) -> None:
    request = RuntimeRequest(
        thread_id=h.thread_id, messages=[Message(role="user", content=[TextContent(text="wait")])]
    )
    await h.runs.claim(
        run_id=uuid4(),
        request_id=request.request_id,
        agent=h.agent,
        thread_id=h.thread_id,
        idempotency_key="stale",
        request_hash=AgentRuntime.request_hash(request),
        owner_token=uuid4(),
        lease_seconds=0.1,
    )
    calls = 0

    class Invoker:
        descriptor = h.agent

        async def invoke(self, request: InvocationInput) -> InvocationOutput:
            nonlocal calls
            calls += 1
            raise AssertionError("Expired work must not be rerun")

    agents = AgentRegistry()
    agents.register(Invoker())
    runtime = AgentRuntime(agents=agents, memory=h.memory, runs=h.runs)
    await asyncio.sleep(0.15)
    with pytest.raises(IdempotentRunUnavailableError):
        await runtime.invoke(h.agent.agent_id, request, idempotency_key="stale")
    assert calls == 0


@pytest.mark.asyncio
async def test_expiry_after_message_flush_rolls_back_batch(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await h.claim(lease=0.2)
    assert run.owner_token is not None
    await h.runs.transition(run.id, "running", owner_token=run.owner_token)

    class DelayedSession(AsyncSession):
        async def flush(self, objects: Sequence[Any] | None = None) -> None:
            messages = any(isinstance(row, AgentMessageTable) for row in self.new)
            await super().flush(objects)
            if messages:
                await asyncio.sleep(0.3)

    monkeypatch.setattr(
        h.runs,
        "_sessions",
        async_sessionmaker(
            h.engine,
            class_=DelayedSession,
            expire_on_commit=False,
        ),
    )
    message = Message(role="assistant", content=[TextContent(text="expired")])
    with pytest.raises(RunOwnershipLostError):
        await h.runs.complete(
            run.id,
            owner_token=run.owner_token,
            messages=[message],
            response=RuntimeResponse(
                run_id=run.id,
                request_id=run.request_id,
                thread_id=h.thread_id,
                agent=h.agent,
                message=message,
            ),
        )
    assert await h.memory.get_messages(h.thread_id) == ()
    assert (await h.runs.recover(run.id)).error_code == "owner_lease_expired"


@pytest.mark.asyncio
async def test_same_key_race_across_threads_has_one_owner(h: Harness) -> None:
    other_thread = await h.memory.create_thread(namespace=h.agent.agent_id)

    async def claim(thread_id: UUID):
        return await h.runs.claim(
            run_id=uuid4(),
            request_id=uuid4(),
            agent=h.agent,
            thread_id=thread_id,
            idempotency_key="racing-key",
            request_hash="same",
            owner_token=uuid4(),
            lease_seconds=60,
        )

    first, second = await asyncio.gather(claim(h.thread_id), claim(other_thread.id))
    assert first.created != second.created
    assert first.run.id == second.run.id
    loser_thread = other_thread.id if first.created else h.thread_id
    # A losing key claim must not occupy its otherwise free thread.
    await h.runs.claim(
        run_id=uuid4(),
        request_id=uuid4(),
        agent=h.agent,
        thread_id=loser_thread,
        idempotency_key=None,
        request_hash="new",
        owner_token=uuid4(),
        lease_seconds=60,
    )
