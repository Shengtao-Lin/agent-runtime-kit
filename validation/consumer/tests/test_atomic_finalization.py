"""Downstream fault injection using installed APIs and public SQLAlchemy events."""

import os
from collections.abc import Sequence
from uuid import UUID

import pytest
from consumer_app import application, request
from sqlalchemy import event

from agent_runtime import Message, RuntimeResponse
from agent_runtime.runs import RunRecord

pytestmark = pytest.mark.skipif(
    os.environ.get("CONSUMER_CANDIDATE") != "1",
    reason="Published v0.1.1 is measured separately by the R3 readiness probe",
)


@pytest.mark.asyncio
async def test_terminal_write_failure_rolls_back_message_batch(
    database_url: str, identity: str
) -> None:
    async with application(database_url, identity) as app:
        triggered = False

        def fail_update(connection, cursor, statement, parameters, context, executemany):
            nonlocal triggered
            del connection, cursor, context, executemany
            values = parameters.values() if isinstance(parameters, dict) else parameters
            if statement.startswith("UPDATE runtime_runs SET") and "succeeded" in values:
                triggered = True
                raise RuntimeError("Synthetic finalization failure")

        event.listen(app.engine.sync_engine, "before_cursor_execute", fail_update)
        try:
            with pytest.raises(RuntimeError, match="Synthetic finalization failure"):
                await app.runtime.invoke(identity, request("hello"), idempotency_key="atomic")
        finally:
            event.remove(app.engine.sync_engine, "before_cursor_execute", fail_update)
        assert triggered
        record = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="atomic")
        assert record is not None and record.status == "failed" and record.response is None
        assert await app.memory.get_messages(record.thread_id) == ()


@pytest.mark.asyncio
async def test_commit_acknowledgment_loss_replays_without_model_work(
    database_url: str, identity: str
) -> None:
    async with application(database_url, identity) as app:
        complete = app.runs.complete

        async def lose_ack(
            run_id: UUID,
            *,
            owner_token: UUID,
            messages: Sequence[Message],
            response: RuntimeResponse,
        ) -> RunRecord:
            await complete(run_id, owner_token=owner_token, messages=messages, response=response)
            raise ConnectionError("Synthetic lost commit acknowledgment")

        app.runs.complete = lose_ack
        first = await app.runtime.invoke(identity, request("hello"), idempotency_key="commit")
        second = await app.runtime.invoke(identity, request("hello"), idempotency_key="commit")
        assert first == second and len(app.model.calls) == 1
        run = await app.runs.get(first.run_id)
        assert run is not None and run.status == "succeeded" and run.response == first
        assert len(await app.memory.get_messages(first.thread_id)) == 2
