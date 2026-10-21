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


async def test_exhausted_pool_times_out_without_work_and_recovers(database_url, identity):
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
        response = await app.runtime.invoke(identity, request("hello"), idempotency_key="pool")
        assert len(app.model.calls) == 1
        assert (await app.runs.get(response.run_id)).status == "succeeded"


async def test_thread_lock_wait_obeys_deadline_and_releases_connection(database_url, identity):
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
            result = await app.runtime.invoke(
                identity, request("hello", thread_id=thread.id), idempotency_key="locked"
            )
            assert (await app.runs.get(result.run_id)).status == "succeeded"
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
