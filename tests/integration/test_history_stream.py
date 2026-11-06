"""Complete cursor snapshots, explicit opt-in and failure-safe history resource ownership."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.context import ContextWindow
from agent_runtime.errors import (
    ContextSelectionError,
    IdempotentRunUnavailableError,
    InvocationTimeoutError,
    ThreadBusyError,
    ThreadNotFoundError,
)
from agent_runtime.memory import MemoryStore, PostgresMemoryStore, StoredMessage
from agent_runtime.models import (
    AgentDescriptor,
    InvocationInput,
    InvocationOutput,
    ToolCall,
    ToolCallContent,
    ToolResultContent,
)
from agent_runtime.runs import PostgresRunStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def message(value: str, *, system: bool = False) -> Message:
    return Message(role="system" if system else "user", content=[TextContent(text=value)])


class Invoker:
    descriptor = AgentDescriptor(agent_id="history", version="1", framework="native")

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.inputs: list[InvocationInput] = []

    async def invoke(self, request: InvocationInput) -> InvocationOutput:
        assert self.engine.pool.checkedout() == 0  # type: ignore[attr-defined]
        self.inputs.append(request)
        return InvocationOutput(
            messages=[Message(role="assistant", content=[TextContent(text="done")])]
        )


class PausedStore(PostgresMemoryStore):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self.entered, self.closed, self.resume = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def stream_messages(
        self, thread_id: UUID, *, namespace: str | None = None
    ) -> AsyncGenerator[StoredMessage, None]:
        try:
            async with aclosing(super().stream_messages(thread_id, namespace=namespace)) as source:
                async for item in source:
                    self.entered.set()
                    await self.resume.wait()
                    yield item
        finally:
            self.closed.set()


class TrackingStore(PostgresMemoryStore):
    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(engine)
        self.seen = 0

    async def stream_messages(
        self, thread_id: UUID, *, namespace: str | None = None
    ) -> AsyncGenerator[StoredMessage, None]:
        async with aclosing(super().stream_messages(thread_id, namespace=namespace)) as source:
            async for item in source:
                self.seen += 1
                yield item


@dataclass
class Host:
    engine: AsyncEngine
    memory: PostgresMemoryStore
    runs: PostgresRunStore
    agents: AgentRegistry
    invoker: Invoker
    namespace: str

    def runtime(self, budget: int = 65536, *, timeout: float = 30) -> AgentRuntime:
        return AgentRuntime(
            agents=self.agents,
            memory=self.memory,
            runs=self.runs,
            namespace=self.namespace,
            context_window=ContextWindow(budget),
            stream_history=True,
            invocation_timeout_seconds=timeout,
        )


@asynccontextmanager
async def host(store_type: type[PostgresMemoryStore] = PostgresMemoryStore) -> AsyncGenerator[Host]:
    url = os.getenv("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    namespace = f"history-test-{uuid4()}"
    engine = create_async_engine(
        url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=5,
        connect_args={"application_name": namespace},
    )
    invoker = Invoker(engine)
    agents = AgentRegistry()
    agents.register(invoker)
    try:
        yield Host(engine, store_type(engine), PostgresRunStore(engine), agents, invoker, namespace)
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    finally:
        await engine.dispose()
    observer = create_async_engine(url)
    try:
        async with observer.connect() as connection:
            assert (
                await connection.scalar(
                    sql("SELECT count(*) FROM pg_stat_activity WHERE application_name=:name"),
                    {"name": namespace},
                )
                == 0
            )
    finally:
        await observer.dispose()


async def test_cursor_retains_one_snapshot_across_batches_and_scopes() -> None:
    async with host() as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = [message(str(index)) for index in range(600)]
        await h.memory.append_messages(thread.id, history)
        async with aclosing(h.memory.stream_messages(thread.id, namespace=h.namespace)) as source:
            first = await anext(source)
            assert h.engine.pool.checkedout() == 1  # type: ignore[attr-defined]
            later = message("appended after cursor snapshot")
            await h.memory.append_messages(thread.id, [later])
            records = [first, *[item async for item in source]]
        assert [item.sequence for item in records] == list(range(1, 601))
        assert [item.message for item in records] == history
        assert [item.message for item in await h.memory.get_messages(thread.id)] == [
            *history,
            later,
        ]
        with pytest.raises(ThreadNotFoundError):
            async with aclosing(h.memory.stream_messages(thread.id, namespace="wrong")) as source:
                await anext(source)
        async with aclosing(h.memory.stream_messages(thread.id)) as source:
            await anext(source)
            # Deliberate early abandonment; the context must close the live cursor/transaction.
        assert h.engine.pool.checkedout() == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize("stream_response", [False, True])
@pytest.mark.parametrize("stop", ["cancel", "timeout"])
async def test_interrupting_history_releases_cursor_and_records_failure(
    stream_response: bool, stop: str
) -> None:
    async with host(PausedStore) as h:
        memory = cast(PausedStore, h.memory)
        thread = await memory.create_thread(namespace=h.namespace)
        history = [message("old") for _ in range(300)]
        await memory.append_messages(thread.id, history)
        request = RuntimeRequest(thread_id=thread.id, messages=[message("now")])
        async with h.runtime(timeout=1 if stop == "timeout" else 30).lifespan() as runtime:

            async def consume() -> None:
                if stream_response:
                    async with aclosing(
                        runtime.stream("history", request, idempotency_key=h.namespace)
                    ) as events:
                        async for _ in events:
                            pass
                else:
                    await runtime.invoke("history", request, idempotency_key=h.namespace)

            task = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(memory.entered.wait(), 3)
                if stop == "cancel":
                    with pytest.raises(ThreadBusyError):
                        await runtime.invoke("history", request, idempotency_key="competing")
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    with pytest.raises(InvocationTimeoutError):
                        await task
                assert memory.closed.is_set()
                assert h.invoker.inputs == []
                assert [item.message for item in await memory.get_messages(thread.id)] == history
                record = await h.runs.get_by_idempotency(
                    agent_id="history", idempotency_key=h.namespace
                )
                assert record is not None and record.status == "failed"
                with pytest.raises(IdempotentRunUnavailableError):
                    await runtime.invoke("history", request, idempotency_key=h.namespace)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("large", [False, True])
async def test_old_policy_and_tool_relationship_crossing_fetch_batch(large: bool) -> None:
    async with host(TrackingStore) as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = [message("old policy", system=True), *[message("old" * 100) for _ in range(254)]]
        history.extend(
            [
                Message(
                    role="assistant",
                    content=[ToolCallContent(tool_call=ToolCall(id="cross", name="tool"))],
                ),
                Message(
                    role="tool",
                    content=[
                        ToolResultContent(
                            tool_call_id="cross",
                            result={"data": "x" * (256 * 1024 if large else 10)},
                        )
                    ],
                ),
                Message(role="assistant", content=[TextContent(text="old answer")]),
            ]
        )
        await h.memory.append_messages(thread.id, history)
        current = [message("now")]
        expected = ContextWindow(4096).select(history, current)
        async with h.runtime(4096).lifespan() as runtime:
            response = await runtime.invoke(
                "history", RuntimeRequest(thread_id=thread.id, messages=current)
            )
        assert h.invoker.inputs[0].messages == list(expected.messages)
        assert h.invoker.inputs[0].messages[0] == history[0]
        assert (history[-2] in h.invoker.inputs[0].messages) is not large
        assert cast(TrackingStore, h.memory).seen == len(history)
        assert [item.message for item in await h.memory.get_messages(thread.id)] == [
            *history,
            *current,
            response.message,
        ]


async def test_omitted_old_malformed_exchange_is_not_hidden_by_cursor_selection() -> None:
    async with host(TrackingStore) as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = [
            Message(role="tool", content=[ToolResultContent(tool_call_id="orphan", result={})]),
            *[message("old" * 100) for _ in range(600)],
        ]
        await h.memory.append_messages(thread.id, history)
        async with h.runtime(1000).lifespan() as runtime:
            with pytest.raises(ContextSelectionError):
                await runtime.invoke(
                    "history",
                    RuntimeRequest(thread_id=thread.id, messages=[message("now")]),
                    idempotency_key=h.namespace,
                )
        assert cast(TrackingStore, h.memory).seen == len(history)
        assert h.invoker.inputs == []
        record = await h.runs.get_by_idempotency(agent_id="history", idempotency_key=h.namespace)
        assert record is not None and record.error_code == "context_selection_failed"
        assert len(await h.memory.get_messages(thread.id)) == len(history)


@pytest.mark.parametrize("earlier_orphan", [False, True])
async def test_corrupt_later_canonical_row_closes_cursor_and_preserves_read_error(
    earlier_orphan: bool,
) -> None:
    async with host(TrackingStore) as h:
        thread = await h.memory.create_thread(namespace=h.namespace)
        history = [message("old") for _ in range(600)]
        if earlier_orphan:
            history[0] = Message(
                role="tool", content=[ToolResultContent(tool_call_id="orphan", result={})]
            )
        await h.memory.append_messages(thread.id, history)
        async with h.engine.begin() as connection:
            await connection.execute(
                sql(
                    "UPDATE agent_messages SET role='invalid' WHERE thread_id=:id AND sequence=550"
                ),
                {"id": thread.id},
            )
        # Legacy reads validate every canonical row before context relationship selection.
        with pytest.raises(ValidationError):
            await h.memory.get_messages(thread.id)
        async with h.runtime(1000).lifespan() as runtime:
            with pytest.raises(ValidationError):
                await runtime.invoke(
                    "history",
                    RuntimeRequest(thread_id=thread.id, messages=[message("now")]),
                    idempotency_key=h.namespace,
                )
        assert cast(TrackingStore, h.memory).seen == 549
        assert h.invoker.inputs == []
        record = await h.runs.get_by_idempotency(agent_id="history", idempotency_key=h.namespace)
        assert record is not None and record.status == "failed"
        async with h.engine.connect() as connection:
            assert (
                await connection.scalar(
                    sql("SELECT count(*) FROM agent_messages WHERE thread_id=:id"),
                    {"id": thread.id},
                )
                == 600
            )


class LegacyStore:
    def __init__(self, delegate: PostgresMemoryStore) -> None:
        self.delegate = delegate
        self.reads = 0

    def __getattr__(self, name: str) -> Any:
        if name == "stream_messages":
            raise AttributeError(name)
        return getattr(self.delegate, name)

    async def get_messages(
        self, thread_id: UUID, *, namespace: str | None = None
    ) -> tuple[StoredMessage, ...]:
        self.reads += 1
        return await self.delegate.get_messages(thread_id, namespace=namespace)


async def test_legacy_store_default_and_explicit_unsupported_mode() -> None:
    async with host() as h:
        legacy = LegacyStore(h.memory)
        options: dict[str, Any] = dict(
            agents=h.agents, memory=cast(MemoryStore, legacy), runs=h.runs, namespace=h.namespace
        )
        with pytest.raises(TypeError, match="StreamingMemoryStore"):
            AgentRuntime(**options, context_window=ContextWindow(4096), stream_history=True)
        with pytest.raises(ValueError, match="context_window"):
            AgentRuntime(**options, stream_history=True)
        with pytest.raises(ValueError, match="boolean"):
            AgentRuntime(**options, stream_history=1)  # type: ignore[arg-type]
        async with AgentRuntime(
            **options, context_window=ContextWindow(4096)
        ).lifespan() as runtime:
            await runtime.invoke("history", RuntimeRequest(messages=[message("now")]))
        assert legacy.reads == 1 and len(h.invoker.inputs) == 1
