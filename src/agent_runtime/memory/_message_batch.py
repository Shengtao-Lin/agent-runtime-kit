"""Shared message append inside a caller-owned PostgreSQL transaction."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.errors import ThreadNotFoundError
from agent_runtime.memory.tables import AgentMessageTable, AgentThreadTable
from agent_runtime.models import Message


async def append_message_batch(
    session: AsyncSession, thread_id: UUID, messages: Sequence[Message]
) -> tuple[AgentMessageTable, ...]:
    """Lock the thread, allocate contiguous ordinals, and flush without committing."""
    if not messages:
        return ()
    thread = (
        await session.execute(
            select(AgentThreadTable).where(AgentThreadTable.id == thread_id).with_for_update()
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
    rows = tuple(
        AgentMessageTable(
            id=message.id,
            thread_id=thread_id,
            sequence=last_sequence + offset,
            role=message.role,
            content=[part.model_dump(mode="json") for part in message.content],
            metadata_={},
        )
        for offset, message in enumerate(messages, start=1)
    )
    session.add_all(rows)
    thread.updated_at = datetime.now(UTC)
    await session.flush()
    return rows
