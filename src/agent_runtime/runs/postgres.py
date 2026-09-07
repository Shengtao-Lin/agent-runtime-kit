"""PostgreSQL durable run repository."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from agent_runtime.errors import (
    IdempotencyConflictError,
    InvalidRunTransitionError,
    RunNotFoundError,
)
from agent_runtime.memory.tables import RuntimeRunTable
from agent_runtime.models import AgentDescriptor, RuntimeResponse
from agent_runtime.runs.models import RunClaim, RunRecord, RunStatus

_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    "accepted": frozenset({"running"}),
    "running": frozenset({"succeeded", "failed", "blocked"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "blocked": frozenset(),
}


class PostgresRunStore:
    """Persist run state and arbitrate per-agent idempotency keys."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def claim(
        self,
        *,
        run_id: UUID,
        request_id: UUID,
        agent: AgentDescriptor,
        thread_id: UUID,
        idempotency_key: str | None,
        request_hash: str,
    ) -> RunClaim:
        """Create an accepted run or return the matching idempotent run."""
        values = {
            "id": run_id,
            "request_id": request_id,
            "agent_id": agent.agent_id,
            "agent_version": agent.version,
            "framework": agent.framework,
            "thread_id": thread_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "status": "accepted",
        }
        statement = insert(RuntimeRunTable).values(**values)
        if idempotency_key is not None:
            statement = statement.on_conflict_do_nothing(
                index_elements=[RuntimeRunTable.agent_id, RuntimeRunTable.idempotency_key],
                index_where=RuntimeRunTable.idempotency_key.is_not(None),
            )
        statement = statement.returning(RuntimeRunTable)
        async with self._sessions.begin() as session:
            created = (await session.execute(statement)).scalar_one_or_none()
            if created is not None:
                return RunClaim(run=self._model(created), created=True)
            if idempotency_key is None:
                raise IdempotencyConflictError("Request identifier is already in use")
            existing = (
                await session.execute(
                    select(RuntimeRunTable).where(
                        RuntimeRunTable.agent_id == agent.agent_id,
                        RuntimeRunTable.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one()
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError(
                    "Idempotency key was already used with a different request"
                )
            return RunClaim(run=self._model(existing), created=False)

    async def transition(
        self,
        run_id: UUID,
        status: RunStatus,
        *,
        response: RuntimeResponse | None = None,
        error_code: str | None = None,
    ) -> RunRecord:
        """Apply one valid transition while holding the run row lock."""
        async with self._sessions.begin() as session:
            row = (
                await session.execute(
                    select(RuntimeRunTable).where(RuntimeRunTable.id == run_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise RunNotFoundError(f"Run '{run_id}' does not exist")
            current = self._status(row.status)
            if status not in _TRANSITIONS[current]:
                raise InvalidRunTransitionError(
                    f"Run cannot transition from '{current}' to '{status}'"
                )
            if status == "succeeded" and response is None:
                raise InvalidRunTransitionError("A succeeded run requires a canonical response")
            if status != "succeeded" and response is not None:
                raise InvalidRunTransitionError("Only a succeeded run may store a response")
            row.status = status
            row.response = response.model_dump(mode="json") if response is not None else None
            row.error_code = error_code
            if status in {"succeeded", "failed", "blocked"}:
                row.completed_at = datetime.now(UTC)
            await session.flush()
            return self._model(row)

    async def get(self, run_id: UUID) -> RunRecord | None:
        """Read a durable run by identifier."""
        async with self._sessions() as session:
            row = (
                await session.execute(select(RuntimeRunTable).where(RuntimeRunTable.id == run_id))
            ).scalar_one_or_none()
        return None if row is None else self._model(row)

    async def get_by_idempotency(self, *, agent_id: str, idempotency_key: str) -> RunRecord | None:
        """Read a run by its per-agent idempotency key."""
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(RuntimeRunTable).where(
                        RuntimeRunTable.agent_id == agent_id,
                        RuntimeRunTable.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
        return None if row is None else self._model(row)

    @staticmethod
    def _status(value: str) -> RunStatus:
        if value not in _TRANSITIONS:
            raise InvalidRunTransitionError(f"Database contains unknown run status '{value}'")
        return value  # type: ignore[return-value]

    @classmethod
    def _model(cls, row: RuntimeRunTable) -> RunRecord:
        response = None if row.response is None else RuntimeResponse.model_validate(row.response)
        return RunRecord(
            id=row.id,
            request_id=row.request_id,
            agent=AgentDescriptor(
                agent_id=row.agent_id,
                version=row.agent_version,
                framework=row.framework,  # type: ignore[arg-type]
            ),
            thread_id=row.thread_id,
            idempotency_key=row.idempotency_key,
            request_hash=row.request_hash,
            status=cls._status(row.status),
            response=response,
            error_code=row.error_code,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )
