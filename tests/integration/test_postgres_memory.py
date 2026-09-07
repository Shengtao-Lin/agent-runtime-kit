from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_runtime.memory import PostgresMemoryStore
from agent_runtime.models import Message, TextContent

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
