"""PostgreSQL append-only feedback repository."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from agent_runtime.errors import IdempotencyConflictError, InvalidFeedbackTargetError
from agent_runtime.feedback.models import (
    FeedbackPage,
    FeedbackRecord,
    FeedbackSource,
    FeedbackSubmission,
    FeedbackTarget,
    FeedbackType,
)
from agent_runtime.memory.tables import FeedbackRecordTable, RuntimeRunTable
from agent_runtime.models import RuntimeResponse


class PostgresFeedbackStore:
    """Persist immutable, target-validated feedback with idempotent retries."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def record(self, submission: FeedbackSubmission) -> FeedbackRecord:
        """Validate ownership and insert feedback, or replay an equivalent retry."""
        payload_hash = self._payload_hash(submission)
        async with self._sessions.begin() as session:
            existing = (
                await session.execute(
                    select(FeedbackRecordTable).where(
                        FeedbackRecordTable.idempotency_key == submission.idempotency_key
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise IdempotencyConflictError(
                        "Feedback idempotency key was used with a different payload"
                    )
                return self._model(existing)

            run = (
                await session.execute(
                    select(RuntimeRunTable).where(RuntimeRunTable.id == submission.run_id)
                )
            ).scalar_one_or_none()
            self._validate_target(run, submission)
            if submission.supersedes_feedback_id is not None:
                superseded = (
                    await session.execute(
                        select(FeedbackRecordTable).where(
                            FeedbackRecordTable.id == submission.supersedes_feedback_id
                        )
                    )
                ).scalar_one_or_none()
                if (
                    superseded is None
                    or superseded.run_id != submission.run_id
                    or superseded.target_type != submission.target_type
                    or superseded.target_id != submission.target_id
                ):
                    raise InvalidFeedbackTargetError(
                        "Superseded feedback must exist and reference the same target"
                    )

            statement = (
                insert(FeedbackRecordTable)
                .values(
                    id=submission.feedback_id,
                    idempotency_key=submission.idempotency_key,
                    payload_hash=payload_hash,
                    run_id=submission.run_id,
                    target_type=submission.target_type,
                    target_id=submission.target_id,
                    source=submission.source,
                    feedback_type=submission.feedback_type,
                    value=submission.value,
                    comment=submission.comment,
                    labels=submission.labels,
                    metadata_=submission.metadata,
                    supersedes_feedback_id=submission.supersedes_feedback_id,
                    event_at=submission.event_at,
                )
                .returning(FeedbackRecordTable)
            )
            row = (await session.execute(statement)).scalar_one()
            return self._model(row)

    async def search(
        self,
        *,
        run_id: UUID | None = None,
        target_type: FeedbackTarget | None = None,
        target_id: str | None = None,
        source: FeedbackSource | None = None,
        feedback_type: FeedbackType | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> FeedbackPage:
        """Return an immutable chronological page with optional categorical filters."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must not be negative")
        conditions: list[ColumnElement[bool]] = []
        if run_id is not None:
            conditions.append(FeedbackRecordTable.run_id == run_id)
        if target_type is not None:
            conditions.append(FeedbackRecordTable.target_type == target_type)
        if target_id is not None:
            conditions.append(FeedbackRecordTable.target_id == target_id)
        if source is not None:
            conditions.append(FeedbackRecordTable.source == source)
        if feedback_type is not None:
            conditions.append(FeedbackRecordTable.feedback_type == feedback_type)
        if created_from is not None:
            conditions.append(FeedbackRecordTable.created_at >= created_from)
        if created_to is not None:
            conditions.append(FeedbackRecordTable.created_at < created_to)
        async with self._sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(FeedbackRecordTable)
                        .where(*conditions)
                        .order_by(FeedbackRecordTable.created_at, FeedbackRecordTable.id)
                        .offset(offset)
                        .limit(limit + 1)
                    )
                ).scalars()
            )
        has_more = len(rows) > limit
        return FeedbackPage(
            items=[self._model(row) for row in rows[:limit]],
            next_offset=offset + limit if has_more else None,
        )

    @staticmethod
    def _validate_target(run: RuntimeRunTable | None, submission: FeedbackSubmission) -> None:
        if run is None or run.status != "succeeded" or run.response is None:
            raise InvalidFeedbackTargetError("Feedback requires a completed successful run")
        response = RuntimeResponse.model_validate(run.response)
        if submission.target_type == "run":
            valid = submission.target_id == str(run.id)
        elif submission.target_type == "message":
            valid = submission.target_id == str(response.message.id)
        else:
            valid = submission.target_id in {str(item.id) for item in response.tool_results}
        if not valid:
            raise InvalidFeedbackTargetError("Feedback target does not belong to the run")

    @staticmethod
    def _payload_hash(submission: FeedbackSubmission) -> str:
        payload = submission.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _model(row: FeedbackRecordTable) -> FeedbackRecord:
        return FeedbackRecord(
            feedback_id=row.id,
            idempotency_key=row.idempotency_key,
            payload_hash=row.payload_hash,
            run_id=row.run_id,
            target_type=row.target_type,  # type: ignore[arg-type]
            target_id=row.target_id,
            source=row.source,  # type: ignore[arg-type]
            feedback_type=row.feedback_type,  # type: ignore[arg-type]
            value=row.value,
            comment=row.comment,
            labels=row.labels,
            metadata=row.metadata_,
            supersedes_feedback_id=row.supersedes_feedback_id,
            event_at=row.event_at,
            created_at=row.created_at,
        )
