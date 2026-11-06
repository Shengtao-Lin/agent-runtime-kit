"""Real-database selection, policy, ownership, failure and replay contracts."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.adapters.base import AgentCompleted, AgentStreamEvent, AgentTextDelta
from agent_runtime.context import ContextWindow
from agent_runtime.errors import (
    ContextBudgetExceededError,
    ContextSelectionError,
    IdempotentRunUnavailableError,
    ThreadBusyError,
)
from agent_runtime.hooks import HookEvent, HookManager
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    RuntimeStreamEvent,
    ToolResultContent,
)
from agent_runtime.runs import PostgresRunStore

pytestmark = pytest.mark.integration

REPLAY_PROCESS = """
import asyncio, json, os, sys
from sqlalchemy.ext.asyncio import create_async_engine
from agent_runtime import AgentRegistry, AgentRuntime, RuntimeRequest
from agent_runtime.context import ContextWindow
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import AgentDescriptor
from agent_runtime.runs import PostgresRunStore
class NoExecution:
    descriptor = AgentDescriptor(agent_id='context', version='1', framework='native')
    async def invoke(self, request):
        raise AssertionError('A restarted process must replay without dispatch')
async def main(payload):
    engine = create_async_engine(os.environ['AGENT_RUNTIME_TEST_DATABASE_URL'])
    registry = AgentRegistry()
    registry.register(NoExecution())
    runtime = AgentRuntime(agents=registry, memory=PostgresMemoryStore(engine),
        runs=PostgresRunStore(engine), namespace=payload['namespace'],
        context_window=ContextWindow(2))
    try:
        async with runtime.lifespan():
            request = RuntimeRequest.model_validate(payload['request'])
            response = await runtime.invoke('context', request,
                idempotency_key=payload['namespace'])
            print(response.model_dump_json())
    finally:
        await engine.dispose()
if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main(json.loads(sys.stdin.read())))
"""


def text(value: str, *, system: bool = False) -> Message:
    return Message(role="system" if system else "user", content=[TextContent(text=value)])


def encoded(messages: list[Message]) -> int:
    return len(
        json.dumps(
            [item.model_dump(mode="json") for item in messages],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    )


class Observer:
    name = "context-observer"

    def __init__(self) -> None:
        self.events: list[HookEvent] = []

    async def handle(self, event: HookEvent) -> None:
        self.events.append(event)


class Invoker:
    descriptor = AgentDescriptor(agent_id="context", version="1", framework="native")

    def __init__(self) -> None:
        self.inputs: list[InvocationInput] = []
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.stream_closed = asyncio.Event()
        self.behavior = "success"

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        self.inputs.append(request)
        self.started.set()
        try:
            if self.behavior == "wait":
                await asyncio.Event().wait()
            if self.behavior == "error":
                raise RuntimeError("synthetic failure")
            return InvocationOutput(
                messages=[Message(role="assistant", content=[TextContent(text="done")])]
            )
        finally:
            self.closed.set()

    async def stream(self, request: InvocationInput) -> AsyncIterator[AgentStreamEvent]:
        try:
            output = await self.invoke(request)
            for _ in range(128):
                yield AgentTextDelta(delta="x")
            yield AgentCompleted(output=output)
        finally:
            self.stream_closed.set()


@dataclass
class Host:
    engine: AsyncEngine
    memory: PostgresMemoryStore
    runs: PostgresRunStore
    registry: AgentRegistry
    invoker: Invoker
    observer: Observer
    namespace: str

    def runtime(self, budget: int | None) -> AgentRuntime:
        return AgentRuntime(
            agents=self.registry,
            memory=self.memory,
            runs=self.runs,
            hooks=HookManager([self.observer]),
            namespace=self.namespace,
            context_window=ContextWindow(budget) if budget is not None else None,
        )


@asynccontextmanager
async def host() -> AsyncGenerator[Host]:
    url = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url, pool_size=2, max_overflow=0, pool_timeout=5)
    invoker = Invoker()
    registry = AgentRegistry()
    registry.register(invoker)
    value = Host(
        engine,
        PostgresMemoryStore(engine),
        PostgresRunStore(engine),
        registry,
        invoker,
        Observer(),
        str(uuid4()),
    )
    try:
        yield value
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
async def test_selected_hook_input_full_history_and_replay_after_policy_change(
    streaming: bool, enabled: bool
) -> None:
    async with host() as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = [text("historical system", system=True), text("old" * 1000)]
        await h.memory.append_messages(thread.id, history)
        current = [text("current one"), text("current two")]
        request = RuntimeRequest(thread_id=thread.id, messages=current)
        expected = [history[0], *current] if enabled else [*history, *current]
        runtime = h.runtime(encoded(expected) if enabled else None)
        async with runtime.lifespan():
            if streaming:
                events: list[RuntimeStreamEvent] = []
                async with aclosing(
                    runtime.stream("context", request, idempotency_key=h.namespace)
                ) as source:
                    async for event in source:
                        events.append(event)
                        if event.type == "text_delta":
                            await asyncio.sleep(0.001)
                assert events[-1].type == "completed"
                response = events[-1].response
            else:
                response = await runtime.invoke("context", request, idempotency_key=h.namespace)
        assert h.invoker.inputs[0].messages == expected
        assert [event.phase for event in h.observer.events] == ["before_model", "after_model"]
        assert h.observer.events[0].messages == expected
        if enabled:
            summary = h.observer.events[0].context.attributes["agent_runtime.context"]
            assert summary["omitted_history_messages"] == 1
            assert summary["selected_bytes"] == encoded(expected)
            assert "old" not in json.dumps(summary)
        else:
            assert "agent_runtime.context" not in h.invoker.inputs[0].context.attributes
        stored = [item.message for item in await h.memory.get_messages(thread.id)]
        assert stored == [*history, *current, response.message]
        # New runtime with an impossible policy still replays before history read/selection/hooks.
        async with h.runtime(2).lifespan() as restarted:
            replay = await restarted.invoke("context", request, idempotency_key=h.namespace)
        assert replay == response
        assert len(h.invoker.inputs) == 1 and len(h.observer.events) == 2
        if enabled and not streaming:
            payload = json.dumps(
                {"namespace": h.namespace, "request": request.model_dump(mode="json")}
            )
            process = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", REPLAY_PROCESS],
                input=payload,
                text=True,
                capture_output=True,
                timeout=15,
                check=True,
            )
            assert json.loads(process.stdout) == response.model_dump(mode="json")


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
async def test_selection_failure_is_durable_without_dispatch_or_history_append(
    streaming: bool, malformed: bool
) -> None:
    async with host() as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = (
            [Message(role="tool", content=[ToolResultContent(tool_call_id="orphan", result={})])]
            if malformed
            else [text("mandatory PRIVATE policy", system=True)]
        )
        await h.memory.append_messages(thread.id, history)
        request = RuntimeRequest(thread_id=thread.id, messages=[text("now")])
        budget = 10000 if malformed else encoded(request.messages)
        error = ContextSelectionError if malformed else ContextBudgetExceededError
        async with h.runtime(budget).lifespan() as runtime:
            with pytest.raises(error) as caught:
                if streaming:
                    async for event in runtime.stream(
                        "context", request, idempotency_key=h.namespace
                    ):
                        assert event.type == "started"
                else:
                    await runtime.invoke("context", request, idempotency_key=h.namespace)
            assert "PRIVATE" not in str(caught.value)
            record = await h.runs.get_by_idempotency(
                agent_id="context", idempotency_key=h.namespace
            )
            assert record is not None and record.status == "failed"
            assert record.error_code == caught.value.code and record.response is None
        async with h.runtime(None).lifespan() as restarted:
            with pytest.raises(IdempotentRunUnavailableError):
                await restarted.invoke("context", request, idempotency_key=h.namespace)
        assert h.invoker.inputs == [] and h.observer.events == []
        assert [item.message for item in await h.memory.get_messages(thread.id)] == history


@pytest.mark.parametrize("behavior", ["wait", "error"])
async def test_selected_failure_cancellation_and_thread_exclusion(behavior: str) -> None:
    async with host() as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = [text("old" * 1000)]
        await h.memory.append_messages(thread.id, history)
        request = RuntimeRequest(thread_id=thread.id, messages=[text("now")])
        h.invoker.behavior = behavior
        async with h.runtime(encoded(request.messages)).lifespan() as runtime:
            task = asyncio.create_task(
                runtime.invoke("context", request, idempotency_key=h.namespace)
            )
            try:
                await asyncio.wait_for(h.invoker.started.wait(), timeout=5)
                if behavior == "wait":
                    async with h.runtime(65536).lifespan() as competing:
                        with pytest.raises(ThreadBusyError):
                            await competing.invoke(
                                "context", request, idempotency_key=h.namespace + "other"
                            )
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(RuntimeError, match="synthetic"):
                        await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert h.invoker.closed.is_set()
            assert h.invoker.inputs[0].messages == request.messages
            record = await h.runs.get_by_idempotency(
                agent_id="context", idempotency_key=h.namespace
            )
            assert record is not None and record.status == "failed"
            assert [item.message for item in await h.memory.get_messages(thread.id)] == history
            with pytest.raises(IdempotentRunUnavailableError):
                await runtime.invoke("context", request, idempotency_key=h.namespace)


async def test_closing_selected_stream_releases_producer_and_preserves_history() -> None:
    async with host() as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = [text("old" * 1000)]
        await h.memory.append_messages(thread.id, history)
        request = RuntimeRequest(thread_id=thread.id, messages=[text("now")])
        async with h.runtime(encoded(request.messages)).lifespan() as runtime:
            async with aclosing(
                runtime.stream("context", request, idempotency_key=h.namespace)
            ) as source:
                async for event in source:
                    if event.type == "text_delta":
                        break
            assert h.invoker.stream_closed.is_set()
            assert h.invoker.inputs[0].messages == request.messages
            record = await h.runs.get_by_idempotency(
                agent_id="context", idempotency_key=h.namespace
            )
            assert record is not None and record.status == "failed"
            assert [item.message for item in await h.memory.get_messages(thread.id)] == history
            with pytest.raises(IdempotentRunUnavailableError):
                await runtime.invoke("context", request, idempotency_key=h.namespace)
