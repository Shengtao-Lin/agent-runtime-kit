"""Public models for PostgreSQL-backed conversation and long-term memory."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from agent_runtime.models import Message, StrictModel


class ThreadRecord(StrictModel):
    """A durable conversation thread."""

    id: UUID
    namespace: str
    external_key: str | None = None
    user_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class StoredMessage(StrictModel):
    """A canonical message plus its thread-local sequence number."""

    thread_id: UUID
    sequence: int
    message: Message
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MemoryRecord(StrictModel):
    """A namespaced long-term memory value."""

    id: UUID
    namespace: str
    user_key: str
    memory_key: str
    value: Any
    tags: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MemoryPage(StrictModel):
    """A stable page of long-term memory records."""

    items: list[MemoryRecord]
    next_offset: int | None = None
