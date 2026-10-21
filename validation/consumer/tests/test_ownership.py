"""Candidate ownership acceptance from independent installed-library consumers."""

import asyncio
import os
import subprocess
import sys

import pytest
from consumer_app import application, request

pytestmark = pytest.mark.skipif(
    os.environ.get("CONSUMER_CANDIDATE") != "1",
    reason="Published v0.1.1 retains frozen concurrency/crash probes",
)


@pytest.mark.asyncio
async def test_distinct_app_instances_reject_overlaps_then_use_fresh_history(
    database_url: str, identity: str
) -> None:
    from agent_runtime.errors import ThreadBusyError

    async with (
        application(database_url, identity) as first_app,
        application(database_url, identity) as second_app,
    ):
        first = await first_app.runtime.invoke(identity, request("first"))
        task = asyncio.create_task(
            first_app.runtime.invoke(identity, request("wait", thread_id=first.thread_id))
        )
        try:
            await asyncio.wait_for(first_app.model.started.wait(), timeout=5)
            with pytest.raises(ThreadBusyError) as error:
                await second_app.runtime.invoke(
                    identity, request("third", thread_id=first.thread_id)
                )
            assert error.value.retryable and second_app.model.calls == []
            first_app.model.release.set()
            await task
            await second_app.runtime.invoke(identity, request("third", thread_id=first.thread_id))
            assert len(second_app.model.calls[0]) == 5
            assert [
                m.sequence for m in await second_app.memory.get_messages(first.thread_id)
            ] == list(range(1, 7))
        finally:
            first_app.model.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_killed_process_recovers_without_automatic_reexecution(
    database_url: str, identity: str
) -> None:
    from agent_runtime.errors import IdempotentRunUnavailableError, ThreadBusyError

    async with application(database_url, identity) as app:
        thread = await app.memory.create_thread(namespace=identity)
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-u", "-m", "lease_worker", identity, str(thread.id), "0.5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout is not None
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=10)
            assert line.strip() == "RUNNING"
            with pytest.raises(ThreadBusyError):
                await app.runtime.invoke(identity, request("new", thread_id=thread.id))
            process.kill()
            await asyncio.to_thread(process.wait, 5)
            await asyncio.sleep(0.6)
            with pytest.raises(IdempotentRunUnavailableError):
                await app.runtime.invoke(
                    identity, request("wait", thread_id=thread.id), idempotency_key="lease-worker"
                )
            assert app.model.calls == []
            run = await app.runs.get_by_idempotency(
                agent_id=identity, idempotency_key="lease-worker"
            )
            assert run is not None and run.error_code == "owner_lease_expired"
            assert await app.memory.get_messages(thread.id) == ()
            await app.runtime.invoke(identity, request("deliberate new turn", thread_id=thread.id))
            assert len(app.model.calls) == 1
        finally:
            if process.poll() is None:
                process.kill()
            await asyncio.to_thread(process.communicate, timeout=5)
