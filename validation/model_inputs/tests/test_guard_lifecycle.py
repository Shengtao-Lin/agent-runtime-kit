"""Cooperative cleanup for host-protected actual-wheel dispatches."""

import asyncio
import os

import pytest
from host_policy import ModelInputBudget, budget_model
from input_cases import growth_host

from agent_runtime import Message, TextContent
from agent_runtime.errors import InvocationTimeoutError
from agent_runtime.models import RuntimeContext
from agent_runtime.models_clients.base import ModelTextDelta


class WaitingModel:
    def __init__(self):
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.calls = 0

    async def generate(self, messages, *, tools=(), context):
        self.calls += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.closed.set()

    async def stream(self, messages, *, tools=(), context):
        self.calls += 1
        self.started.set()
        try:
            yield ModelTextDelta(delta="provisional")
            await asyncio.Event().wait()
        finally:
            self.closed.set()


@pytest.mark.parametrize("mode", ["invoke", "stream", "fallback"])
@pytest.mark.parametrize("stop", ["timeout", "cancel"])
@pytest.mark.parametrize("framework", [None, "langchain", "langgraph"])
async def test_runtime_deadline_and_cancellation_close_protected_source(mode, stop, framework):
    tasks_before = set(asyncio.all_tasks())
    delegate = WaitingModel()
    async with growth_host(
        os.environ["DATABASE_URL"],
        "oversized_tool",
        protected=True,
        generate_only=mode == "fallback",
        model_override=delegate,
        framework=framework,
        model_stream=mode == "stream",
        invocation_seconds=0.3 if stop == "timeout" else 30,
    ) as app:
        events = []

        async def execute():
            request = app.request()
            if mode == "invoke":
                return await app.runtime.invoke(app.namespace, request, idempotency_key="waiting")
            async for event in app.runtime.stream(
                app.namespace, request, idempotency_key="waiting"
            ):
                events.append(event)

        task = asyncio.create_task(execute())
        await asyncio.wait_for(delegate.started.wait(), timeout=5)
        if stop == "cancel":
            task.cancel()
        with pytest.raises(InvocationTimeoutError if stop == "timeout" else asyncio.CancelledError):
            await task
        assert delegate.closed.is_set()
        assert delegate.calls == 1
        record = await app.runs.get_by_idempotency(
            agent_id=app.namespace, idempotency_key="waiting"
        )
        assert record.status == "failed" and record.response is None
        assert record.error_code == (
            "invocation_timeout" if stop == "timeout" else "invocation_cancelled"
        )
        assert not await app.memory.get_messages(record.thread_id, namespace=app.namespace)
        assert not any(event.type == "completed" for event in events)
    await asyncio.sleep(0)
    assert not (set(asyncio.all_tasks()) - tasks_before)
    assert app.cleanup == {"pool_checkouts": 0, "database_sessions": 0}


async def test_consumer_closes_wrapper_iterator():
    from uuid import uuid4

    delegate = WaitingModel()
    wrapped = budget_model(delegate, ModelInputBudget(65536, 8192))
    source = wrapped.stream(
        [Message(role="user", content=[TextContent(text="review")])],
        context=RuntimeContext(run_id=uuid4(), thread_id=uuid4()),
    )
    assert (await anext(source)).delta == "provisional"
    await source.aclose()
    assert delegate.closed.is_set()
    assert delegate.calls == 1


async def test_runtime_stream_consumer_close_settles_failure():
    delegate = WaitingModel()
    tasks_before = set(asyncio.all_tasks())
    async with growth_host(
        os.environ["DATABASE_URL"], "oversized_tool", protected=True, model_override=delegate
    ) as app:
        source = app.runtime.stream(app.namespace, app.request(), idempotency_key="close")
        async for event in source:
            if event.type == "text_delta":
                break
        await source.aclose()
        assert delegate.closed.is_set()
        record = await app.runs.get_by_idempotency(agent_id=app.namespace, idempotency_key="close")
        assert record.status == "failed" and record.error_code == "invocation_cancelled"
        assert not await app.memory.get_messages(record.thread_id, namespace=app.namespace)
    await asyncio.sleep(0)
    assert not (set(asyncio.all_tasks()) - tasks_before)
    assert app.cleanup == {"pool_checkouts": 0, "database_sessions": 0}
