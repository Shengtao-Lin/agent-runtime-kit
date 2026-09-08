"""Append-only runtime feedback capture."""

from typing import TYPE_CHECKING

from agent_runtime.feedback.base import FeedbackStore
from agent_runtime.feedback.models import FeedbackPage, FeedbackRecord, FeedbackSubmission

if TYPE_CHECKING:
    from agent_runtime.feedback.postgres import PostgresFeedbackStore

__all__ = [
    "FeedbackPage",
    "FeedbackRecord",
    "FeedbackStore",
    "FeedbackSubmission",
    "PostgresFeedbackStore",
]


def __getattr__(name: str) -> object:
    """Load the optional PostgreSQL implementation only when requested."""
    if name == "PostgresFeedbackStore":
        from agent_runtime.feedback.postgres import PostgresFeedbackStore

        return PostgresFeedbackStore
    raise AttributeError(name)
