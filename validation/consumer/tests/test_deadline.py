"""Candidate-only deadline acceptance through the installed library."""

import asyncio
import os

import pytest
from consumer_app import application, request

from agent_runtime.errors import InvocationTimeoutError
from agent_runtime.hooks import HookEvent, HookManager

pytestmark = pytest.mark.skipif(
    os.environ.get("CONSUMER_CANDIDATE") != "1",
    reason="Published v0.1.1 keeps its frozen R4 readiness probe",
)


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize("phase", ["before_model", "after_model"])
@pytest.mark.asyncio
async def test_hook_is_inside_total_deadline(
    database_url: str, identity: str, mode: str, phase: str
) -> None:
    entered = asyncio.Event()

    class BlockingHook:
        name = "candidate-deadline"

        async def handle(self, event: HookEvent) -> None:
            if event.phase == phase:
                entered.set()
                await asyncio.Event().wait()

    async with application(
        database_url,
        identity,
        invocation_deadline_seconds=0.5,
        hooks=HookManager([BlockingHook()]),
    ) as app:
        async with asyncio.timeout(5):
            with pytest.raises(InvocationTimeoutError):
                if mode == "invoke":
                    await app.runtime.invoke(identity, request("hello"), idempotency_key="deadline")
                else:
                    async for _ in app.runtime.stream(
                        identity, request("hello"), idempotency_key="deadline"
                    ):
                        pass
        assert entered.is_set()
        record = await app.runs.get_by_idempotency(agent_id=identity, idempotency_key="deadline")
        assert record is not None and record.status == "failed"
        assert record.error_code == "invocation_timeout" and record.response is None
        assert await app.memory.get_messages(record.thread_id) == ()
