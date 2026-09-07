"""Append-only feedback persistence protocol."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_runtime.feedback.models import (
    FeedbackPage,
    FeedbackRecord,
    FeedbackSource,
    FeedbackSubmission,
    FeedbackTarget,
    FeedbackType,
)


class FeedbackStore(Protocol):
    """Record idempotent feedback and export immutable pages."""

    async def record(self, submission: FeedbackSubmission) -> FeedbackRecord: ...

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
    ) -> FeedbackPage: ...
