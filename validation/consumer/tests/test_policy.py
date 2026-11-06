"""Durable policy composition through the installed native runtime."""

import asyncio
import os

import pytest
from consumer_app import application, request

from agent_runtime.errors import IdempotentRunUnavailableError
from agent_runtime.hooks import GuardrailBlockedError, GuardrailDecision, HookManager

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("CONSUMER_CANDIDATE") != "1", reason="Candidate policy acceptance"
    ),
]


class Policy:
    name = "policy"

    def __init__(self, stop=None, failure="block"):
        self.stop = stop
        self.failure = failure
        self.events = []
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def handle(self, event):
        self.events.append(event)
        if event.phase != self.stop:
            return
        if self.failure == "block":
            raise GuardrailBlockedError(GuardrailDecision(allowed=False, reason_code="synthetic"))
        if self.failure == "error":
            raise ValueError("synthetic hook failure")
        self.entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("stop", [None, "before_model", "before_tool", "after_tool", "after_model"])
async def test_policy_order_blocking_and_replay(database_url, identity, streaming, stop):
    policy = Policy(stop)
    async with application(database_url, identity, hooks=HookManager([policy])) as app:
        observed = []
        response = None

        async def execute():
            nonlocal response
            if streaming:
                async for event in app.runtime.stream(
                    identity, request("double 7"), idempotency_key="policy"
                ):
                    observed.append(event.type)
                    if event.type == "completed":
                        response = event.response
            else:
                response = await app.runtime.invoke(
                    identity, request("double 7"), idempotency_key="policy"
                )

        try:
            if stop is None:
                await execute()
            else:
                with pytest.raises(GuardrailBlockedError):
                    await execute()
            phases = ["before_model", "before_tool", "after_tool", "after_model"]
            expected = phases if stop is None else phases[: phases.index(stop) + 1]
            assert [event.phase for event in policy.events] == expected
            assert len({event.context.run_id for event in policy.events}) == 1
            record = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="policy")
            assert record is not None
            history = await app.memory.get_messages(record.thread_id)
            assert len(app.model.calls) == (
                0 if stop == "before_model" else 1 if stop in ("before_tool", "after_tool") else 2
            )
            if stop is None:
                assert record.status == "succeeded" and len(history) == 4
                assert (
                    await app.runtime.invoke(
                        identity, request("double 7"), idempotency_key="policy"
                    )
                    == response
                )
            else:
                assert record.status == "blocked" and record.error_code == "guardrail_blocked"
                assert record.response is None and history == ()
                assert "completed" not in observed
                with pytest.raises(IdempotentRunUnavailableError):
                    await app.runtime.invoke(
                        identity, request("double 7"), idempotency_key="policy"
                    )
            assert [event.phase for event in policy.events] == expected
            if stop == "after_tool":
                assert policy.events[-1].tool_result.output == {"value": 14}
                assert "tool_result" not in observed
            if streaming and stop == "after_model":
                assert "text_delta" in observed and "tool_result" in observed
        finally:
            assert await app.runtime.shutdown(timeout_seconds=3)


@pytest.mark.parametrize("phase", ["before_tool", "after_model"])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_policy_failure_or_cancellation_settles_without_reexecution(
    database_url, identity, phase, failure
):
    policy = Policy(phase, failure)
    async with application(database_url, identity, hooks=HookManager([policy])) as app:
        task = asyncio.create_task(
            app.runtime.invoke(identity, request("double 7"), idempotency_key="failure")
        )
        try:
            if failure == "cancel":
                async with asyncio.timeout(3):
                    await policy.entered.wait()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                assert policy.cancelled.is_set()
            else:
                with pytest.raises(ValueError, match="synthetic hook failure"):
                    await task
            record = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="failure")
            assert record.status == "failed" and record.response is None
            assert record.error_code == (
                "invocation_cancelled" if failure == "cancel" else "internal_error"
            )
            assert await app.memory.get_messages(record.thread_id) == ()
            calls = len(policy.events)
            with pytest.raises(IdempotentRunUnavailableError):
                await app.runtime.invoke(identity, request("double 7"), idempotency_key="failure")
            assert len(policy.events) == calls
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert await app.runtime.shutdown(timeout_seconds=3)
