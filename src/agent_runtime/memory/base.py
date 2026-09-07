"""Persistence protocol usable independently from the full runtime."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from agent_runtime.memory.models import MemoryPage, MemoryRecord, StoredMessage, ThreadRecord
from agent_runtime.models import Message


class MemoryStore(Protocol):
    """Async contract for conversation and namespaced long-term memory."""

    async def create_thread(
        self,
        *,
        namespace: str,
        external_key: str | None = None,
        user_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ThreadRecord: ...

    async def get_thread(self, thread_id: UUID) -> ThreadRecord | None: ...

    async def append_messages(
        self, thread_id: UUID, messages: Sequence[Message]
    ) -> tuple[StoredMessage, ...]: ...

    async def get_messages(self, thread_id: UUID) -> tuple[StoredMessage, ...]: ...

    async def put(
        self,
        *,
        namespace: str,
        user_key: str,
        memory_key: str,
        value: Any,
        tags: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryRecord: ...

    async def get(
        self, *, namespace: str, user_key: str, memory_key: str
    ) -> MemoryRecord | None: ...

    async def search(
        self,
        *,
        namespace: str,
        user_key: str,
        tags: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> MemoryPage: ...

    async def delete(self, *, namespace: str, user_key: str, memory_key: str) -> bool: ...
