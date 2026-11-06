"""Candidate operating boundaries against real pools and PostgreSQL locks."""

import asyncio
import os

import pytest
from consumer_app import application, request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime.errors import InvocationTimeoutError

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("CONSUMER_CANDIDATE") != "1", reason="Candidate fault acceptance"
    ),
]


def delay_recovery_model(model, monkeypatch):
    """Recovery is a correctness probe, not a 200 ms model/transaction latency SLO."""
    generate = model.generate

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.25)
        return await generate(*args, **kwargs)

    monkeypatch.setattr(model, "generate", delayed)


async def test_exhausted_pool_times_out_without_work_and_recovers(
    database_url, identity, monkeypatch
):
    async with application(database_url, identity, invocation_deadline_seconds=0.2) as app:
        held = [await app.engine.connect() for _ in range(4)]
        try:
            async with asyncio.timeout(3):
                with pytest.raises(InvocationTimeoutError):
                    await app.runtime.invoke(identity, request("hello"), idempotency_key="pool")
            assert app.model.calls == []
        finally:
            for connection in held:
                await connection.close()
        assert await app.memory.check_ready()
        assert await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="pool") is None
        assert app.engine.pool.checkedout() == 0
        # Retain the exhausted pool itself, but use the normal invocation budget for recovery.
        async with application(database_url, identity, engine=app.engine) as recovered:
            assert recovered.engine is app.engine
            delay_recovery_model(recovered.model, monkeypatch)
            response = await recovered.runtime.invoke(
                identity, request("hello"), idempotency_key="pool"
            )
            assert len(recovered.model.calls) == 1 and app.model.calls == []
            assert (await app.runs.get(response.run_id)).status == "succeeded"
        assert app.engine.pool.checkedout() == 0


async def test_thread_lock_wait_obeys_deadline_and_releases_connection(
    database_url, identity, monkeypatch
):
    control = create_async_engine(database_url, pool_size=1, max_overflow=0)
    try:
        async with application(database_url, identity, invocation_deadline_seconds=0.2) as app:
            thread = await app.memory.create_thread(namespace=identity)
            async with control.begin() as blocker:
                await blocker.execute(
                    text("SELECT id FROM agent_threads WHERE id=:id FOR UPDATE"), {"id": thread.id}
                )
                async with asyncio.timeout(3):
                    with pytest.raises(InvocationTimeoutError):
                        await app.runtime.invoke(
                            identity,
                            request("hello", thread_id=thread.id),
                            idempotency_key="locked",
                        )
                assert app.model.calls == []
                assert app.engine.pool.checkedout() == 0
            assert (
                await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="locked")
                is None
            )
            assert await app.memory.get_messages(thread.id, namespace=identity) == ()
            async with application(database_url, identity, engine=app.engine) as recovered:
                assert recovered.engine is app.engine
                delay_recovery_model(recovered.model, monkeypatch)
                result = await recovered.runtime.invoke(
                    identity, request("hello", thread_id=thread.id), idempotency_key="locked"
                )
                assert len(recovered.model.calls) == 1 and app.model.calls == []
                assert (await app.runs.get(result.run_id)).status == "succeeded"
            assert app.engine.pool.checkedout() == 0
    finally:
        await control.dispose()


async def test_pre_ping_replaces_a_terminated_idle_database_connection(database_url, identity):
    control = create_async_engine(database_url, pool_size=1, max_overflow=0)
    try:
        async with application(database_url, identity) as app:
            async with app.engine.connect() as connection:
                backend = await connection.scalar(text("SELECT pg_backend_pid()"))
            async with control.connect() as connection:
                assert await connection.scalar(
                    text("SELECT pg_terminate_backend(:pid)"), {"pid": backend}
                )
            assert await app.memory.check_ready()
            result = await app.runtime.invoke(identity, request("hello"))
            assert (await app.runs.get(result.run_id)).status == "succeeded"
            assert len(app.model.calls) == 1
    finally:
        await control.dispose()
