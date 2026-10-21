"""Real framework consumers tested against a wheel outside the source checkout."""

import asyncio
from uuid import uuid4

import pytest
from consumer_app import request
from framework_app import application

from agent_runtime.errors import AgentInvocationError
from agent_runtime.feedback import FeedbackSubmission

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
async def test_framework_history_tools_feedback_and_restart(database_url, identity, framework):
    async with application(database_url, identity, framework) as app:
        first = await app.runtime.invoke(identity, request("quote"), idempotency_key="first")
        assert app.tool_calls == [3]
        assert first.tool_results[0].tool_name == "quote"
        assert first.tool_results[0].output == 375
        stored = await app.memory.get_messages(first.thread_id, namespace=identity)
        assert [item.message.role for item in stored] == ["user", "assistant", "tool", "assistant"]
        events = [
            event
            async for event in app.runtime.stream(
                identity, request("again", thread_id=first.thread_id), idempotency_key="second"
            )
        ]
        assert [event.type for event in events] == ["started", "completed"]
        assert app.seen_history == [1, 5]
        assert len(await app.memory.get_messages(first.thread_id, namespace=identity)) == 8
        submission = FeedbackSubmission(
            feedback_id=uuid4(),
            idempotency_key=f"feedback-{identity}",
            run_id=first.run_id,
            target_type="message",
            target_id=str(first.message.id),
            source="user",
            feedback_type="thumb",
            value=True,
        )
        recorded = await app.feedback.record(submission)
        assert await app.feedback.record(submission) == recorded

    async with application(database_url, identity, framework) as restarted:
        replay = await restarted.runtime.invoke(identity, request("quote"), idempotency_key="first")
        assert replay == first
        assert restarted.tool_calls == [] and restarted.seen_history == []


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
async def test_framework_failure_does_not_commit_history(database_url, identity, framework):
    async with application(database_url, identity, framework) as app:
        with pytest.raises(AgentInvocationError) as caught:
            await app.runtime.invoke(identity, request("fail"), idempotency_key="fail")
        assert "private-framework-failure" not in str(caught.value)
        record = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="fail")
        assert record.status == "failed" and record.response is None
        assert await app.memory.get_messages(record.thread_id, namespace=identity) == ()


@pytest.mark.parametrize("framework", ["langchain", "langgraph"])
async def test_host_shutdown_cancels_framework_and_settles_run(database_url, identity, framework):
    async with application(database_url, identity, framework) as app:
        task = asyncio.create_task(
            app.runtime.invoke(identity, request("wait"), idempotency_key="wait")
        )
        async with asyncio.timeout(5):
            await app.entered.wait()
            assert await app.runtime.shutdown(timeout_seconds=3)
            with pytest.raises(asyncio.CancelledError):
                await task
        assert app.cancelled.is_set()
        record = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="wait")
        assert record.status == "failed" and record.error_code == "invocation_cancelled"
        assert await app.memory.get_messages(record.thread_id, namespace=identity) == ()
