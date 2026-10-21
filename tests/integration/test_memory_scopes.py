"""Explicit namespace checks without claiming application identity isolation."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agent_runtime import AgentRegistry, AgentRuntime, Message, RuntimeRequest, TextContent
from agent_runtime.errors import ThreadNotFoundError
from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import AgentDescriptor, InvocationInput, InvocationOutput
from agent_runtime.runs import PostgresRunStore

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    url = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not url:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def store(engine: AsyncEngine) -> PostgresMemoryStore:
    return PostgresMemoryStore(engine)


@pytest.mark.asyncio
async def test_scoped_thread_reads_and_appends_reject_foreign_ids(
    store: PostgresMemoryStore,
) -> None:
    scope = f"scope-{uuid4()}"
    thread = await store.create_thread(namespace=scope)
    message = Message(role="user", content=[TextContent(text="synthetic")])
    assert await store.get_thread(thread.id, namespace="foreign") is None
    assert await store.get_thread(thread.id, namespace=scope) == thread
    with pytest.raises(ThreadNotFoundError):
        await store.get_messages(thread.id, namespace="foreign")
    for batch in ([message], []):
        with pytest.raises(ThreadNotFoundError):
            await store.append_messages(thread.id, batch, namespace="foreign")
    assert await store.get_messages(thread.id, namespace=scope) == ()
    await store.append_messages(thread.id, [message], namespace=scope)
    assert (await store.get_messages(thread.id, namespace=scope))[0].message == message
    # Legacy unscoped maintenance APIs remain explicit, not a tenancy promise.
    assert (await store.get_thread(thread.id)) is not None


@pytest.mark.asyncio
async def test_expiry_cleanup_can_be_restricted_to_one_namespace(
    store: PostgresMemoryStore,
) -> None:
    a, b = f"expiry-{uuid4()}", f"expiry-{uuid4()}"
    for scope in (a, b):
        await store.put(
            namespace=scope,
            user_key="subject",
            memory_key="expired",
            value="synthetic",
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    assert await store.cleanup_expired(namespace=a) == 1
    assert await store.cleanup_expired(namespace=a) == 0
    assert await store.cleanup_expired(namespace=b) == 1


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.asyncio
async def test_runtime_cannot_continue_or_replay_a_foreign_namespace(
    store: PostgresMemoryStore, engine: AsyncEngine, mode: str
) -> None:
    class Invoker:
        descriptor = AgentDescriptor(agent_id=f"scoped-{uuid4()}", version="1", framework="native")
        calls = 0

        async def invoke(self, request: InvocationInput) -> InvocationOutput:
            self.calls += 1
            return InvocationOutput(
                messages=[Message(role="assistant", content=[TextContent(text="ok")])]
            )

    invoker = Invoker()
    registry = AgentRegistry()
    registry.register(invoker)
    # Both runtimes deliberately share agent ID, engine, and idempotency-key domain.
    runs = PostgresRunStore(engine)
    owner = AgentRuntime(agents=registry, memory=store, runs=runs, namespace="owner")
    foreign = AgentRuntime(agents=registry, memory=store, runs=runs, namespace="foreign")
    request = RuntimeRequest(messages=[Message(role="user", content=[TextContent(text="hello")])])
    response = await owner.invoke(invoker.descriptor.agent_id, request, idempotency_key="key")
    assert invoker.calls == 1
    for continuation, key in (
        (request, "key"),
        (request.model_copy(update={"thread_id": response.thread_id}), None),
    ):
        with pytest.raises(ThreadNotFoundError):
            if mode == "invoke":
                await foreign.invoke(invoker.descriptor.agent_id, continuation, idempotency_key=key)
            else:
                async for _ in foreign.stream(
                    invoker.descriptor.agent_id, continuation, idempotency_key=key
                ):
                    pass
    assert invoker.calls == 1
    assert (
        await owner.invoke(invoker.descriptor.agent_id, request, idempotency_key="key") == response
    )
    assert len(await store.get_messages(response.thread_id, namespace="owner")) == 2


@pytest.mark.asyncio
async def test_foreign_replay_does_not_recover_expired_owner(
    store: PostgresMemoryStore, engine: AsyncEngine
) -> None:
    import asyncio

    thread = await store.create_thread(namespace="owner")
    runs = PostgresRunStore(engine)
    agent = AgentDescriptor(agent_id=f"pending-{uuid4()}", version="1", framework="native")
    request = RuntimeRequest(
        thread_id=thread.id, messages=[Message(role="user", content=[TextContent(text="hello")])]
    )
    claim = await runs.claim(
        run_id=uuid4(),
        request_id=request.request_id,
        agent=agent,
        thread_id=thread.id,
        idempotency_key="pending",
        request_hash=AgentRuntime.request_hash(request),
        owner_token=uuid4(),
        lease_seconds=0.1,
    )

    class Invoker:
        descriptor = agent

        async def invoke(self, request: InvocationInput) -> InvocationOutput:
            raise AssertionError("Foreign work must not execute")

    registry = AgentRegistry()
    registry.register(Invoker())
    runtime = AgentRuntime(agents=registry, memory=store, runs=runs, namespace="foreign")
    await asyncio.sleep(0.15)
    with pytest.raises(ThreadNotFoundError):
        await runtime.invoke(agent.agent_id, request, idempotency_key="pending")
    persisted = await runs.get(claim.run.id)
    assert persisted is not None and persisted.status == "accepted"
