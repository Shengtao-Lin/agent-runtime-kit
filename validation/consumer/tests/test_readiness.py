"""Executable v0.2.0 acceptance targets; strict xfails expose measured v0.1.1 gaps."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest
from consumer_app import application, request

from agent_runtime.errors import InvocationTimeoutError
from agent_runtime.hooks import HookEvent, HookManager

pytestmark = pytest.mark.readiness


class SlowHook:
    name = "slow-consumer-policy"

    async def handle(self, event: HookEvent) -> None:
        if event.phase == "before_model":
            await asyncio.sleep(0.1)


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="v0.1.1 excludes hooks from the invocation deadline",
)
@pytest.mark.asyncio
async def test_deadline_includes_before_model_policy(database_url: str, identity: str) -> None:
    async with application(
        database_url, identity, invocation_deadline_seconds=0.02, hooks=HookManager([SlowHook()])
    ) as app:
        with pytest.raises(InvocationTimeoutError):
            await app.runtime.invoke(identity, request("hello"))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="v0.1.1 reads same-thread histories before either call commits",
)
@pytest.mark.asyncio
async def test_same_thread_calls_use_serial_history(database_url: str, identity: str) -> None:
    async with application(database_url, identity, invocation_deadline_seconds=10) as app:
        first = await app.runtime.invoke(identity, request("first"))
        started = asyncio.Event()
        second_entered = asyncio.Event()
        release = asyncio.Event()
        histories: list[int] = []
        original = app.model.generate

        async def synchronize(messages: object, **kwargs: object) -> object:
            histories.append(len(messages))
            if len(histories) == 1:
                started.set()
                await asyncio.wait_for(release.wait(), timeout=5)
            else:
                second_entered.set()
            return await original(messages, **kwargs)

        app.model.generate = synchronize
        tasks = [
            asyncio.create_task(
                app.runtime.invoke(identity, request("second", thread_id=first.thread_id))
            )
        ]
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            tasks.append(
                asyncio.create_task(
                    app.runtime.invoke(identity, request("third", thread_id=first.thread_id))
                )
            )
            # Current runtimes reach the second model deterministically. A future
            # serializing runtime keeps it waiting until the first turn commits.
            try:
                await asyncio.wait_for(second_entered.wait(), timeout=2)
            except TimeoutError:
                pass
            release.set()
            await asyncio.gather(*tasks)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert sorted(histories) == [3, 5]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="v0.1.1 appends messages before committing terminal run state",
)
@pytest.mark.asyncio
async def test_failed_finalization_does_not_commit_messages(
    database_url: str, identity: str
) -> None:
    async with application(database_url, identity) as app:
        transition = app.runs.transition

        async def fail_success(run_id: object, status: str, **kwargs: object) -> object:
            if status == "succeeded":
                raise RuntimeError("Synthetic terminal write failure")
            return await transition(run_id, status, **kwargs)

        app.runs.transition = fail_success
        with pytest.raises(RuntimeError, match="Synthetic terminal write failure"):
            await app.runtime.invoke(identity, request("hello"), idempotency_key="finalize")
        run = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="finalize")
        assert run is not None and run.status == "failed"
        assert await app.memory.get_messages(run.thread_id) == ()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="v0.1.1 has no lease or recovery path for an abruptly killed owner",
)
@pytest.mark.asyncio
async def test_abrupt_owner_exit_has_a_recovery_path(database_url: str, identity: str) -> None:
    process = await asyncio.to_thread(
        subprocess.Popen,
        [sys.executable, "-u", "-m", "crash_worker", identity],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "AGENT_RUNTIME_TEST_DATABASE_URL": database_url},
    )
    try:
        assert process.stdout is not None
        line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), timeout=10)
        if line.strip() != "RUNNING":
            raise RuntimeError("Crash worker failed to reach the model boundary")
        process.kill()
        await asyncio.to_thread(process.wait, 5)
        async with application(database_url, identity) as app:
            run = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="crash")
            if run is None:
                raise RuntimeError("Crash worker did not persist its run")
            assert run.status in {"failed", "succeeded", "blocked"}
    finally:
        if process.poll() is None:
            process.kill()
        await asyncio.to_thread(process.communicate, timeout=5)
