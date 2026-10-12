"""Acceptance checks against an installed wheel, without repository imports."""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from consumer_app import application, request

import agent_runtime
from agent_runtime.errors import (
    IdempotencyConflictError,
    InvocationTimeoutError,
    RunInProgressError,
)
from agent_runtime.feedback import FeedbackSubmission
from agent_runtime.hooks import (
    GuardrailBlockedError,
    GuardrailHook,
    HookManager,
    PhraseBlockGuardrail,
)


def test_package_is_installed_from_a_wheel() -> None:
    assert Path(agent_runtime.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert agent_runtime.__version__ == os.environ.get("CONSUMER_EXPECTED_VERSION", "0.1.1")
    distribution = importlib.metadata.distribution("agent-runtime-kit")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert direct_url["url"].endswith(".whl")
    assert not direct_url.get("dir_info", {}).get("editable", False)
    assert importlib.util.find_spec("examples") is None
    assert importlib.util.find_spec("fastapi") is None
    assert importlib.util.find_spec("langchain_core") is None


@pytest.mark.asyncio
async def test_invoke_history_and_replay_after_application_restart(
    database_url: str, identity: str
) -> None:
    async with application(database_url, identity) as app:
        assert await app.memory.check_ready()
        assert await app.memory.check_migrations(
            os.environ.get("CONSUMER_EXPECTED_REVISION", "0001")
        )
        first = await app.runtime.invoke(identity, request("hello"), idempotency_key="first")
        assert len(app.model.calls) == 1
    assert app.model.closed
    assert app.engine.pool.checkedout() == 0

    async with application(database_url, identity) as restarted:
        replay = await restarted.runtime.invoke(identity, request("hello"), idempotency_key="first")
        assert replay.model_dump(mode="json") == first.model_dump(mode="json")
        assert restarted.model.calls == []
        continued = await restarted.runtime.invoke(
            identity, request("continue", thread_id=first.thread_id)
        )
        assert continued.thread_id == first.thread_id
        assert len(restarted.model.calls[-1]) == 3
        stored = await restarted.memory.get_messages(first.thread_id)
        assert [item.sequence for item in stored] == [1, 2, 3, 4]
        with pytest.raises(IdempotencyConflictError):
            await restarted.runtime.invoke(identity, request("changed"), idempotency_key="first")


@pytest.mark.asyncio
async def test_tool_stream_and_terminal_replay(database_url: str, identity: str) -> None:
    async with application(database_url, identity) as app:
        events = [
            event
            async for event in app.runtime.stream(
                identity, request("double 7"), idempotency_key="stream"
            )
        ]
        assert [event.type for event in events] == [
            "started",
            "tool_result",
            "text_delta",
            "text_delta",
            "completed",
        ]
        terminal = events[-1]
        assert terminal.type == "completed"
        assert terminal.response.tool_results[0].output == {"value": 14}
        assert (
            "".join(event.delta for event in events if event.type == "text_delta") == "Result: 14"
        )
        run = await app.runs.get(terminal.response.run_id)
        assert run is not None and run.status == "succeeded"
        assert run.response == terminal.response
        calls = len(app.model.calls)
        replay = [
            event
            async for event in app.runtime.stream(
                identity, request("double 7"), idempotency_key="stream"
            )
        ]
        assert [event.type for event in replay] == ["started", "completed"]
        assert replay[-1] == terminal
        assert len(app.model.calls) == calls


@pytest.mark.asyncio
async def test_feedback_correction_and_offset_export(database_url: str, identity: str) -> None:
    async with application(database_url, identity) as app:
        response = await app.runtime.invoke(identity, request("hello"))
        original = FeedbackSubmission(
            feedback_id=uuid4(),
            idempotency_key=f"feedback-{identity}",
            run_id=response.run_id,
            target_type="message",
            target_id=str(response.message.id),
            source="reviewer",
            feedback_type="thumb",
            value=False,
        )
        recorded = await app.feedback.record(original)
        assert await app.feedback.record(original) == recorded
        correction = original.model_copy(
            update={
                "feedback_id": uuid4(),
                "idempotency_key": f"correction-{identity}",
                "value": True,
                "supersedes_feedback_id": recorded.feedback_id,
            }
        )
        corrected = await app.feedback.record(correction)
        first = await app.feedback.search(run_id=response.run_id, limit=1)
        assert first.items == [recorded] and first.next_offset == 1
        second = await app.feedback.search(
            run_id=response.run_id, limit=1, offset=first.next_offset
        )
        assert second.items == [corrected] and second.next_offset is None


@pytest.mark.asyncio
async def test_standalone_memory_namespace_and_delete(database_url: str, identity: str) -> None:
    async with application(database_url, identity) as app:
        await app.memory.put(
            namespace=identity,
            user_key="synthetic",
            memory_key="language",
            value={"language": "en"},
            tags={"kind": "preference"},
        )
        page = await app.memory.search(
            namespace=identity, user_key="synthetic", tags={"kind": "preference"}
        )
        assert len(page.items) == 1 and page.items[0].value == {"language": "en"}
        assert (
            await app.memory.get(
                namespace=f"other-{identity}", user_key="synthetic", memory_key="language"
            )
            is None
        )
        assert await app.memory.delete(
            namespace=identity, user_key="synthetic", memory_key="language"
        )
        assert (
            await app.memory.get(namespace=identity, user_key="synthetic", memory_key="language")
            is None
        )


@pytest.mark.asyncio
async def test_guardrail_and_timeout_leave_terminal_records(
    database_url: str, identity: str
) -> None:
    hooks = HookManager([GuardrailHook(PhraseBlockGuardrail(["blocked"]), phases={"before_model"})])
    async with application(
        database_url, identity, invocation_deadline_seconds=0.1, hooks=hooks
    ) as app:
        with pytest.raises(GuardrailBlockedError):
            await app.runtime.invoke(identity, request("blocked"), idempotency_key="blocked")
        assert app.model.calls == []
        blocked = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="blocked")
        assert blocked is not None and blocked.status == "blocked"
        assert await app.memory.get_messages(blocked.thread_id) == ()
        with pytest.raises(InvocationTimeoutError):
            await app.runtime.invoke(identity, request("wait"), idempotency_key="timeout")
        timed_out = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="timeout")
        assert timed_out is not None and timed_out.status == "failed"
        assert timed_out.error_code == "invocation_timeout"


@pytest.mark.asyncio
async def test_closing_stream_cancels_model_and_marks_failure(
    database_url: str, identity: str
) -> None:
    async with application(database_url, identity) as app:
        stream = app.runtime.stream(identity, request("wait"), idempotency_key="cancel")
        try:
            assert (await anext(stream)).type == "started"
            await asyncio.wait_for(app.model.started.wait(), timeout=2)
        finally:
            await stream.aclose()
        run = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="cancel")
        assert run is not None and run.status == "failed"
        assert run.error_code == "invocation_cancelled"
        assert await app.memory.get_messages(run.thread_id) == ()


@pytest.mark.asyncio
async def test_concurrent_identical_request_has_one_owner(database_url: str, identity: str) -> None:
    async with application(database_url, identity) as app:
        task = asyncio.create_task(
            app.runtime.invoke(identity, request("wait"), idempotency_key="same")
        )
        try:
            await asyncio.wait_for(app.model.started.wait(), timeout=2)
            with pytest.raises(RunInProgressError):
                await app.runtime.invoke(identity, request("wait"), idempotency_key="same")
            app.model.release.set()
            first = await task
            replay = await app.runtime.invoke(identity, request("wait"), idempotency_key="same")
            assert replay == first and len(app.model.calls) == 1
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
