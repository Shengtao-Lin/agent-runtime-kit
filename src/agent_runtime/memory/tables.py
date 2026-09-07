"""SQLAlchemy tables for the PostgreSQL persistence boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Declarative metadata root used by Alembic."""


class AgentThreadTable(Base):
    __tablename__ = "agent_threads"
    __table_args__ = (
        Index(
            "uq_agent_threads_namespace_external_key",
            "namespace",
            "external_key",
            unique=True,
            postgresql_where=text("external_key IS NOT NULL"),
        ),
        Index("ix_agent_threads_user", "namespace", "user_key"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    namespace: Mapped[str] = mapped_column(Text)
    external_key: Mapped[str | None] = mapped_column(Text)
    user_key: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentMessageTable(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_agent_messages_thread_sequence"),
        Index("ix_agent_messages_thread_created", "thread_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("agent_threads.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryRecordTable(Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        UniqueConstraint("namespace", "user_key", "memory_key", name="uq_memory_records_identity"),
        Index("ix_memory_records_subject", "namespace", "user_key"),
        Index("ix_memory_records_expiry", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    namespace: Mapped[str] = mapped_column(Text)
    user_key: Mapped[str] = mapped_column(Text)
    memory_key: Mapped[str] = mapped_column(Text)
    value: Mapped[Any] = mapped_column(JSONB)
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RuntimeRunTable(Base):
    __tablename__ = "runtime_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('accepted', 'running', 'succeeded', 'failed', 'blocked')",
            name="ck_runtime_runs_status",
        ),
        Index(
            "uq_runtime_runs_agent_idempotency",
            "agent_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_runtime_runs_thread_started", "thread_id", "started_at"),
        Index("ix_runtime_runs_agent_started", "agent_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True)
    agent_id: Mapped[str] = mapped_column(Text)
    agent_version: Mapped[str] = mapped_column(Text)
    framework: Mapped[str] = mapped_column(Text)
    thread_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("agent_threads.id"))
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeedbackRecordTable(Base):
    __tablename__ = "feedback_records"
    __table_args__ = (
        Index("ix_feedback_records_run_created", "run_id", "created_at"),
        Index("ix_feedback_records_target", "target_type", "target_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    payload_hash: Mapped[str] = mapped_column(Text)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("runtime_runs.id"))
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    feedback_type: Mapped[str] = mapped_column(Text)
    value: Mapped[Any] = mapped_column(JSONB)
    comment: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[list[str]] = mapped_column(JSONB, default=list)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    supersedes_feedback_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("feedback_records.id")
    )
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
