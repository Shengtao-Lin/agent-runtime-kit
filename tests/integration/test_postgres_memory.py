from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from agent_runtime.errors import (
    IdempotencyConflictError,
    InvalidFeedbackTargetError,
    InvalidRunTransitionError,
)
from agent_runtime.feedback import FeedbackSubmission, PostgresFeedbackStore
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import AgentDescriptor, Message, RuntimeResponse, TextContent
from agent_runtime.runs import PostgresRunStore

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if value is None:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    return value


@pytest.mark.asyncio
async def test_thread_messages_and_long_term_memory_round_trip() -> None:
    store = PostgresMemoryStore.from_url(database_url())
    namespace = f"integration-{uuid4()}"
    try:
        assert await store.check_ready()
        thread = await store.create_thread(
            namespace=namespace,
            external_key="conversation-1",
            user_key="synthetic-user",
        )
        repeated = await store.create_thread(
            namespace=namespace,
            external_key="conversation-1",
            user_key="synthetic-user",
        )
        assert repeated.id == thread.id

        first = Message(role="user", content=[TextContent(text="First")])
        second = Message(role="assistant", content=[TextContent(text="Second")])
        await asyncio.gather(
            store.append_messages(thread.id, [first]),
            store.append_messages(thread.id, [second]),
        )
        stored = await store.get_messages(thread.id)
        assert [item.sequence for item in stored] == [1, 2]
        assert {item.message.id for item in stored} == {first.id, second.id}

        record = await store.put(
            namespace=namespace,
            user_key="synthetic-user",
            memory_key="locale",
            value={"language": "en"},
            tags={"kind": "preference"},
        )
        loaded = await store.get(
            namespace=namespace,
            user_key="synthetic-user",
            memory_key="locale",
        )
        assert loaded is not None
        assert loaded.id == record.id
        assert loaded.value == {"language": "en"}

        page = await store.search(
            namespace=namespace,
            user_key="synthetic-user",
            tags={"kind": "preference"},
        )
        assert [item.memory_key for item in page.items] == ["locale"]

        await store.put(
            namespace=namespace,
            user_key="synthetic-user",
            memory_key="expired",
            value=True,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        assert (
            await store.get(
                namespace=namespace,
                user_key="synthetic-user",
                memory_key="expired",
            )
            is None
        )
        assert await store.cleanup_expired() == 1
        assert await store.delete(
            namespace=namespace,
            user_key="synthetic-user",
            memory_key="locale",
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_run_idempotency_state_and_append_only_feedback() -> None:
    url = database_url()
    memory = PostgresMemoryStore.from_url(url)
    engine = create_async_engine(url)
    runs = PostgresRunStore(engine)
    feedback = PostgresFeedbackStore(engine)
    suffix = str(uuid4())
    try:
        thread = await memory.create_thread(namespace=f"runs-{suffix}")
        run_id = uuid4()
        request_id = uuid4()
        agent = AgentDescriptor(agent_id="integration-agent", version="1", framework="native")
        claim = await runs.claim(
            run_id=run_id,
            request_id=request_id,
            agent=agent,
            thread_id=thread.id,
            idempotency_key=f"invoke-{suffix}",
            request_hash="same-request",
        )
        assert claim.created
        replay = await runs.claim(
            run_id=uuid4(),
            request_id=uuid4(),
            agent=agent,
            thread_id=thread.id,
            idempotency_key=f"invoke-{suffix}",
            request_hash="same-request",
        )
        assert not replay.created
        assert replay.run.id == run_id
        with pytest.raises(IdempotencyConflictError):
            await runs.claim(
                run_id=uuid4(),
                request_id=uuid4(),
                agent=agent,
                thread_id=thread.id,
                idempotency_key=f"invoke-{suffix}",
                request_hash="different-request",
            )

        await runs.transition(run_id, "running")
        assistant = Message(role="assistant", content=[TextContent(text="Completed")])
        response = RuntimeResponse(
            run_id=run_id,
            request_id=request_id,
            thread_id=thread.id,
            agent=agent,
            message=assistant,
        )
        completed = await runs.transition(run_id, "succeeded", response=response)
        assert completed.response == response
        with pytest.raises(InvalidRunTransitionError):
            await runs.transition(run_id, "failed", error_code="too_late")

        submission = FeedbackSubmission(
            feedback_id=uuid4(),
            idempotency_key=f"feedback-{suffix}",
            run_id=run_id,
            target_type="message",
            target_id=str(assistant.id),
            source="user",
            feedback_type="thumb",
            value=True,
        )
        recorded = await feedback.record(submission)
        repeated = await feedback.record(submission)
        assert repeated.feedback_id == recorded.feedback_id
        with pytest.raises(IdempotencyConflictError):
            await feedback.record(submission.model_copy(update={"value": False}))
        with pytest.raises(InvalidFeedbackTargetError):
            await feedback.record(
                submission.model_copy(
                    update={
                        "feedback_id": uuid4(),
                        "idempotency_key": f"bad-target-{suffix}",
                        "target_id": str(uuid4()),
                    }
                )
            )

        correction = await feedback.record(
            FeedbackSubmission(
                feedback_id=uuid4(),
                idempotency_key=f"correction-{suffix}",
                run_id=run_id,
                target_type="message",
                target_id=str(assistant.id),
                source="reviewer",
                feedback_type="correction",
                value="Use a clearer answer.",
                supersedes_feedback_id=recorded.feedback_id,
            )
        )
        assert correction.supersedes_feedback_id == recorded.feedback_id
        page = await feedback.search(run_id=run_id, limit=10)
        assert [item.feedback_id for item in page.items] == [
            recorded.feedback_id,
            correction.feedback_id,
        ]
    finally:
        await memory.close()
        await engine.dispose()
