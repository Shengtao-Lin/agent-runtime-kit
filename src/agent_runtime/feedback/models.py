"""Append-only feedback contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from agent_runtime.models import StrictModel

FeedbackTarget = Literal["run", "message", "tool_result"]
FeedbackSource = Literal["user", "reviewer", "system"]
FeedbackType = Literal["thumb", "rating", "correction", "comment", "label"]


class FeedbackSubmission(StrictModel):
    """Caller-provided feedback linked to one completed runtime artifact."""

    feedback_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=256)
    run_id: UUID
    target_type: FeedbackTarget
    target_id: str = Field(min_length=1, max_length=256)
    source: FeedbackSource
    feedback_type: FeedbackType
    value: bool | int | float | str | dict[str, Any]
    comment: str | None = Field(default=None, max_length=8_000)
    labels: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)
    supersedes_feedback_id: UUID | None = None
    event_at: datetime | None = None

    @model_validator(mode="after")
    def validate_target(self) -> FeedbackSubmission:
        if self.target_type == "run" and self.target_id != str(self.run_id):
            raise ValueError("run feedback target_id must equal run_id")
        return self


class FeedbackRecord(FeedbackSubmission):
    """Immutable feedback with its server receipt timestamp."""

    payload_hash: str
    created_at: datetime


class FeedbackPage(StrictModel):
    """A chronological page for future evaluation export."""

    items: list[FeedbackRecord]
    next_offset: int | None = None
