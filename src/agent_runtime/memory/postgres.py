"""Async PostgreSQL implementation of conversation and long-term memory."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from agent_runtime.memory.models import MemoryPage, MemoryRecord, StoredMessage, ThreadRecord
from agent_runtime.memory.tables import AgentMessageTable, AgentThreadTable, MemoryRecordTable
from agent_runtime.models import Message


class ThreadNotFoundError(LookupError):
    """Raised when a conversation thread does not exist."""


class PostgresMemoryStore:
    """PostgreSQL-backed memory usable without constructing `AgentRuntime`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_url(
        cls,
        database_url: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 5,
        pool_timeout: float = 5.0,
    ) -> PostgresMemoryStore:
        """Construct a store with a bounded SQLAlchemy connection pool."""
        engine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_pre_ping=True,
        )
        return cls(engine)

    async def close(self) -> None:
        """Release all pooled database connections."""
        await self._engine.dispose()

    async def check_ready(self) -> bool:
        """Return whether a database connection can execute a trivial query."""
        try:
            async with self._engine.connect() as connection:
                await connection.execute(select(1))
        except Exception:
            return False
        return True

    async def create_thread(
        self,
        *,
        namespace: str,
        external_key: str | None = None,
        user_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ThreadRecord:
        """Create a thread or return the existing external-key thread."""
        thread_id = uuid4()
        statement = insert(AgentThreadTable).values(
            id=thread_id,
            namespace=namespace,
            external_key=external_key,
            user_key=user_key,
            metadata_=metadata or {},
        )
        if external_key is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=[AgentThreadTable.namespace, AgentThreadTable.external_key],
                index_where=AgentThreadTable.external_key.is_not(None),
            )
        async with self._sessions.begin() as session:
            await session.execute(statement)
            query = select(AgentThreadTable).where(AgentThreadTable.id == thread_id)
            if external_key is not None:
                query = select(AgentThreadTable).where(
                    AgentThreadTable.namespace == namespace,
                    AgentThreadTable.external_key == external_key,
                )
            row = (await session.execute(query)).scalar_one()
        return self._thread_model(row)

    async def append_messages(
        self, thread_id: UUID, messages: Sequence[Message]
    ) -> tuple[StoredMessage, ...]:
        """Append messages atomically while holding a per-thread row lock."""
        if not messages:
            return ()
        async with self._sessions.begin() as session:
            thread = (
                await session.execute(
                    select(AgentThreadTable)
                    .where(AgentThreadTable.id == thread_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if thread is None:
                raise ThreadNotFoundError(f"Thread '{thread_id}' does not exist")
            last_sequence = (
                await session.execute(
                    select(func.coalesce(func.max(AgentMessageTable.sequence), 0)).where(
                        AgentMessageTable.thread_id == thread_id
                    )
                )
            ).scalar_one()
            rows: list[AgentMessageTable] = []
            for offset, message in enumerate(messages, start=1):
                row = AgentMessageTable(
                    id=message.id,
                    thread_id=thread_id,
                    sequence=last_sequence + offset,
                    role=message.role,
                    content=[part.model_dump(mode="json") for part in message.content],
                    metadata_={},
                )
                session.add(row)
                rows.append(row)
            thread.updated_at = datetime.now(UTC)
            await session.flush()
        return tuple(self._message_model(row) for row in rows)

    async def get_thread(self, thread_id: UUID) -> ThreadRecord | None:
        """Read one thread by identifier."""
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(AgentThreadTable).where(AgentThreadTable.id == thread_id)
                )
            ).scalar_one_or_none()
        return None if row is None else self._thread_model(row)

    async def get_messages(self, thread_id: UUID) -> tuple[StoredMessage, ...]:
        """Read a thread's messages in deterministic sequence order."""
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(AgentMessageTable)
                    .where(AgentMessageTable.thread_id == thread_id)
                    .order_by(AgentMessageTable.sequence)
                )
            ).scalars()
            return tuple(self._message_model(row) for row in rows)

    async def put(
        self,
        *,
        namespace: str,
        user_key: str,
        memory_key: str,
        value: Any,
        tags: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryRecord:
        """Create or replace one namespaced memory value."""
        now = datetime.now(UTC)
        statement = (
            insert(MemoryRecordTable)
            .values(
                id=uuid4(),
                namespace=namespace,
                user_key=user_key,
                memory_key=memory_key,
                value=value,
                tags=tags or {},
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                constraint="uq_memory_records_identity",
                set_={
                    "value": value,
                    "tags": tags or {},
                    "expires_at": expires_at,
                    "updated_at": now,
                },
            )
            .returning(MemoryRecordTable)
        )
        async with self._sessions.begin() as session:
            row = (await session.execute(statement)).scalar_one()
        return self._memory_model(row)

    async def get(self, *, namespace: str, user_key: str, memory_key: str) -> MemoryRecord | None:
        """Return an unexpired memory value, if present."""
        now = datetime.now(UTC)
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(MemoryRecordTable).where(
                        MemoryRecordTable.namespace == namespace,
                        MemoryRecordTable.user_key == user_key,
                        MemoryRecordTable.memory_key == memory_key,
                        or_(
                            MemoryRecordTable.expires_at.is_(None),
                            MemoryRecordTable.expires_at > now,
                        ),
                    )
                )
            ).scalar_one_or_none()
        return None if row is None else self._memory_model(row)

    async def search(
        self,
        *,
        namespace: str,
        user_key: str,
        tags: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> MemoryPage:
        """Page through unexpired records using optional JSON tag containment."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must not be negative")
        now = datetime.now(UTC)
        conditions = [
            MemoryRecordTable.namespace == namespace,
            MemoryRecordTable.user_key == user_key,
            or_(MemoryRecordTable.expires_at.is_(None), MemoryRecordTable.expires_at > now),
        ]
        if tags:
            conditions.append(MemoryRecordTable.tags.contains(tags))
        async with self._sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(MemoryRecordTable)
                        .where(*conditions)
                        .order_by(MemoryRecordTable.memory_key)
                        .offset(offset)
                        .limit(limit + 1)
                    )
                ).scalars()
            )
        has_more = len(rows) > limit
        items = [self._memory_model(row) for row in rows[:limit]]
        return MemoryPage(items=items, next_offset=offset + limit if has_more else None)

    async def delete(self, *, namespace: str, user_key: str, memory_key: str) -> bool:
        """Delete one namespaced record and report whether it existed."""
        async with self._sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(MemoryRecordTable).where(
                        MemoryRecordTable.namespace == namespace,
                        MemoryRecordTable.user_key == user_key,
                        MemoryRecordTable.memory_key == memory_key,
                    )
                ),
            )
        return bool(result.rowcount)

    async def cleanup_expired(self, *, before: datetime | None = None) -> int:
        """Explicitly delete expired records; no background scheduler is created."""
        cutoff = before or datetime.now(UTC)
        async with self._sessions.begin() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    delete(MemoryRecordTable).where(MemoryRecordTable.expires_at <= cutoff)
                ),
            )
        return int(result.rowcount or 0)

    @staticmethod
    def _thread_model(row: AgentThreadTable) -> ThreadRecord:
        return ThreadRecord(
            id=row.id,
            namespace=row.namespace,
            external_key=row.external_key,
            user_key=row.user_key,
            metadata=row.metadata_,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _message_model(row: AgentMessageTable) -> StoredMessage:
        return StoredMessage(
            thread_id=row.thread_id,
            sequence=row.sequence,
            message=Message.model_validate(
                {"id": row.id, "role": row.role, "content": row.content}
            ),
            metadata=row.metadata_,
            created_at=row.created_at,
        )

    @staticmethod
    def _memory_model(row: MemoryRecordTable) -> MemoryRecord:
        return MemoryRecord(
            id=row.id,
            namespace=row.namespace,
            user_key=row.user_key,
            memory_key=row.memory_key,
            value=row.value,
            tags=row.tags,
            expires_at=row.expires_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
