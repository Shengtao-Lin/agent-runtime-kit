"""PostgreSQL durable runs, fenced ownership, and thread arbitration."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from math import isfinite
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from agent_runtime.errors import (
    IdempotencyConflictError,
    InvalidRunTransitionError,
    RunNotFoundError,
    RunOwnershipLostError,
    ThreadBusyError,
    ThreadNotFoundError,
)
from agent_runtime.memory._message_batch import append_message_batch
from agent_runtime.memory.tables import AgentThreadTable, RuntimeRunTable
from agent_runtime.models import AgentDescriptor, Message, RuntimeResponse
from agent_runtime.runs.models import RunClaim, RunRecord, RunStatus
from agent_runtime.telemetry import RuntimeTelemetry

_ACTIVE = ("accepted", "running")
_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    "accepted": frozenset({"running"}),
    "running": frozenset({"failed", "blocked"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "blocked": frozenset(),
}


class PostgresRunStore:
    """Arbitrate per-agent keys and one live turn per thread across processes."""

    def __init__(self, engine: AsyncEngine, telemetry: RuntimeTelemetry | None = None) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)
        self._telemetry = telemetry or RuntimeTelemetry()

    async def claim(
        self,
        *,
        run_id: UUID,
        request_id: UUID,
        agent: AgentDescriptor,
        thread_id: UUID,
        idempotency_key: str | None,
        request_hash: str,
        owner_token: UUID,
        lease_seconds: float,
    ) -> RunClaim:
        """Claim a key and thread in one short transaction, rejecting live overlaps."""
        if not isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be finite and positive")
        async with self._sessions.begin() as session:
            await self._lock_thread(session, thread_id)
            if idempotency_key is not None:
                existing = (
                    await session.execute(
                        select(RuntimeRunTable).where(
                            RuntimeRunTable.agent_id == agent.agent_id,
                            RuntimeRunTable.idempotency_key == idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    self._check_hash(existing, request_hash)
                    # The key may belong to another thread: recovery must lock that thread first.
                    return RunClaim(run=self._model(existing), created=False)
            active = (
                await session.execute(
                    select(RuntimeRunTable)
                    .where(
                        RuntimeRunTable.thread_id == thread_id,
                        RuntimeRunTable.status.in_(_ACTIVE),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            now = await self._now(session)
            if active is not None:
                if not self._expire(active, now):
                    raise ThreadBusyError("Another request owns this conversation thread")
                await session.flush()
            statement = insert(RuntimeRunTable).values(
                id=run_id,
                request_id=request_id,
                agent_id=agent.agent_id,
                agent_version=agent.version,
                framework=agent.framework,
                thread_id=thread_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="accepted",
                owner_token=owner_token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            if idempotency_key is not None:
                statement = statement.on_conflict_do_nothing(
                    index_elements=[RuntimeRunTable.agent_id, RuntimeRunTable.idempotency_key],
                    index_where=RuntimeRunTable.idempotency_key.is_not(None),
                )
            created = (
                await session.execute(statement.returning(RuntimeRunTable))
            ).scalar_one_or_none()
            if created is not None:
                return RunClaim(run=self._model(created), created=True)
            existing = (
                await session.execute(
                    select(RuntimeRunTable).where(
                        RuntimeRunTable.agent_id == agent.agent_id,
                        RuntimeRunTable.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one()
            self._check_hash(existing, request_hash)
            return RunClaim(run=self._model(existing), created=False)

    async def transition(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        owner_token: UUID,
        response: RuntimeResponse | None = None,
        error_code: str | None = None,
    ) -> RunRecord:
        """Apply a valid transition only for the unexpired owner."""
        if status == "succeeded":
            raise InvalidRunTransitionError("Successful runs must be finalized with complete()")
        async with self._sessions.begin() as session:
            row = await self._lock_run(session, run_id)
            now = await self._now(session)
            self._check_owner(row, owner_token, now)
            if status not in _TRANSITIONS[self._status(row.status)]:
                raise InvalidRunTransitionError(
                    f"Run cannot transition from '{row.status}' to '{status}'"
                )
            if response is not None:
                raise InvalidRunTransitionError("Only a succeeded run may store a response")
            row.status, row.error_code = status, error_code
            if status in {"failed", "blocked"}:
                row.completed_at = now
            await session.flush()
            return self._model(row)

    async def complete(
        self,
        run_id: UUID,
        *,
        owner_token: UUID,
        messages: Sequence[Message],
        response: RuntimeResponse,
    ) -> RunRecord:
        """Commit history and replay together, fenced against expired or foreign owners."""
        with self._telemetry.span(
            "agent.memory.write",
            {
                "agent.memory.operation": "complete_run",
                "agent.run.id": str(run_id),
                "agent.thread.id": str(response.thread_id),
                "agent.message.count": len(messages),
            },
        ):
            async with self._sessions.begin() as session:
                row = await self._lock_run(session, run_id)
                self._check_owner(row, owner_token, await self._now(session))
                if row.status != "running":
                    raise InvalidRunTransitionError("Only a running run can be completed")
                if (
                    response.run_id != row.id
                    or response.request_id != row.request_id
                    or response.thread_id != row.thread_id
                    or response.agent.agent_id != row.agent_id
                    or response.agent.version != row.agent_version
                    or response.agent.framework != row.framework
                ):
                    raise InvalidRunTransitionError(
                        "Completion response does not belong to the run"
                    )
                assistant = next((m for m in reversed(messages) if m.role == "assistant"), None)
                if assistant != response.message:
                    raise InvalidRunTransitionError(
                        "Completion message does not match the response"
                    )
                await append_message_batch(session, row.thread_id, messages)
                # Message writes may block: check again at the final persistence boundary.
                now = await self._now(session)
                self._check_owner(row, owner_token, now)
                row.status = "succeeded"
                row.response = response.model_dump(mode="json")
                row.error_code, row.completed_at = None, now
                await session.flush()
                result = self._model(row)
            return result

    async def fail_if_running(
        self,
        run_id: UUID,
        *,
        owner_token: UUID,
        error_code: str,
        status: Literal["failed", "blocked"] = "failed",
    ) -> RunRecord:
        """Settle this owner's unfinished row without changing terminal results."""
        if status not in {"failed", "blocked"}:
            raise InvalidRunTransitionError("Cleanup status must be failed or blocked")
        async with self._sessions.begin() as session:
            row = await self._lock_run(session, run_id)
            if row.owner_token != owner_token:
                raise RunOwnershipLostError("Run owner token does not match")
            now = await self._now(session)
            if row.status in _ACTIVE and not self._expire(row, now):
                row.status, row.error_code, row.completed_at = status, error_code, now
                row.response = None
            await session.flush()
            return self._model(row)

    async def recover(self, run_id: UUID) -> RunRecord:
        """Fail an expired owner using database time; never re-execute its work."""
        async with self._sessions.begin() as session:
            row = await self._lock_run(session, run_id)
            self._expire(row, await self._now(session))
            await session.flush()
            return self._model(row)

    async def recover_expired(self, *, limit: int = 100) -> tuple[RunRecord, ...]:
        """Sweep a bounded batch, skipping threads held by live finalizers."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        async with self._sessions() as session:
            ids = tuple(
                (
                    await session.scalars(
                        select(RuntimeRunTable.id)
                        .where(
                            RuntimeRunTable.status.in_(_ACTIVE),
                            RuntimeRunTable.lease_expires_at <= func.clock_timestamp(),
                        )
                        .order_by(RuntimeRunTable.lease_expires_at, RuntimeRunTable.id)
                        .limit(limit)
                    )
                ).all()
            )
        recovered: list[RunRecord] = []
        for run_id in ids:
            async with self._sessions.begin() as session:
                thread_id = await session.scalar(
                    select(RuntimeRunTable.thread_id).where(RuntimeRunTable.id == run_id)
                )
                if thread_id is None:
                    continue
                locked = await session.scalar(
                    select(AgentThreadTable.id)
                    .where(AgentThreadTable.id == thread_id)
                    .with_for_update(skip_locked=True)
                )
                if locked is None:
                    continue
                row = (
                    await session.execute(
                        select(RuntimeRunTable)
                        .where(RuntimeRunTable.id == run_id)
                        .with_for_update()
                    )
                ).scalar_one()
                if self._expire(row, await self._now(session)):
                    await session.flush()
                    recovered.append(self._model(row))
        return tuple(recovered)

    async def get(self, run_id: UUID) -> RunRecord | None:
        async with self._sessions() as session:
            row = await session.get(RuntimeRunTable, run_id)
            return self._model(row) if row is not None else None

    async def get_by_idempotency(self, *, agent_id: str, idempotency_key: str) -> RunRecord | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(RuntimeRunTable).where(
                        RuntimeRunTable.agent_id == agent_id,
                        RuntimeRunTable.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            return self._model(row) if row is not None else None

    @staticmethod
    async def _now(session: AsyncSession) -> datetime:
        return cast(datetime, await session.scalar(select(func.clock_timestamp())))

    @staticmethod
    async def _lock_thread(session: AsyncSession, thread_id: UUID) -> None:
        thread = await session.scalar(
            select(AgentThreadTable.id).where(AgentThreadTable.id == thread_id).with_for_update()
        )
        if thread is None:
            raise ThreadNotFoundError(f"Thread '{thread_id}' does not exist")

    @classmethod
    async def _lock_run(cls, session: AsyncSession, run_id: UUID) -> RuntimeRunTable:
        thread_id = await session.scalar(
            select(RuntimeRunTable.thread_id).where(RuntimeRunTable.id == run_id)
        )
        if thread_id is None:
            raise RunNotFoundError(f"Run '{run_id}' does not exist")
        # Every mutation uses the same thread -> run lock order.
        await cls._lock_thread(session, thread_id)
        return (
            await session.execute(
                select(RuntimeRunTable).where(RuntimeRunTable.id == run_id).with_for_update()
            )
        ).scalar_one()

    @staticmethod
    def _expire(row: RuntimeRunTable, now: datetime) -> bool:
        if (
            row.status in _ACTIVE
            and row.lease_expires_at is not None
            and row.lease_expires_at <= now
        ):
            row.status, row.error_code, row.completed_at = "failed", "owner_lease_expired", now
            row.response = None
            return True
        return False

    @staticmethod
    def _check_owner(row: RuntimeRunTable, owner_token: UUID, now: datetime) -> None:
        if row.owner_token != owner_token or (
            row.status in _ACTIVE and (row.lease_expires_at is None or row.lease_expires_at <= now)
        ):
            raise RunOwnershipLostError("Run owner is invalid or its lease has expired")

    @staticmethod
    def _check_hash(row: RuntimeRunTable, request_hash: str) -> None:
        if row.request_hash != request_hash:
            raise IdempotencyConflictError(
                "Idempotency key was already used with a different request"
            )

    @staticmethod
    def _status(value: str) -> RunStatus:
        if value not in _TRANSITIONS:
            raise InvalidRunTransitionError(f"Database contains unknown run status '{value}'")
        return value

    @classmethod
    def _model(cls, row: RuntimeRunTable) -> RunRecord:
        return RunRecord(
            id=row.id,
            request_id=row.request_id,
            agent=AgentDescriptor.model_validate(
                {
                    "agent_id": row.agent_id,
                    "version": row.agent_version,
                    "framework": row.framework,
                }
            ),
            thread_id=row.thread_id,
            idempotency_key=row.idempotency_key,
            request_hash=row.request_hash,
            status=cls._status(row.status),
            response=RuntimeResponse.model_validate(row.response)
            if row.response is not None
            else None,
            error_code=row.error_code,
            started_at=row.started_at,
            completed_at=row.completed_at,
            owner_token=row.owner_token,
            lease_expires_at=row.lease_expires_at,
        )
