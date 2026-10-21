"""Candidate-only namespace contract through public installed APIs."""

import os

import pytest
from consumer_app import application, request

from agent_runtime.errors import ThreadNotFoundError

pytestmark = pytest.mark.skipif(
    os.environ.get("CONSUMER_CANDIDATE") != "1", reason="Candidate namespace contract"
)


@pytest.mark.asyncio
async def test_memory_accepts_scoped_conversation_operations(
    database_url: str, identity: str
) -> None:
    async with application(database_url, identity) as app:
        thread = await app.memory.create_thread(namespace=identity)
        assert await app.memory.get_thread(thread.id, namespace="foreign") is None
        with pytest.raises(ThreadNotFoundError):
            await app.memory.get_messages(thread.id, namespace="foreign")
        with pytest.raises(ThreadNotFoundError):
            await app.memory.append_messages(
                thread.id, request("hello").messages, namespace="foreign"
            )
        assert await app.memory.get_messages(thread.id, namespace=identity) == ()
