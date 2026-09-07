"""Create the initial runtime persistence schema.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all MVP persistence tables and indexes."""
    op.create_table(
        "agent_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("external_key", sa.Text(), nullable=True),
        sa.Column("user_key", sa.Text(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "uq_agent_threads_namespace_external_key",
        "agent_threads",
        ["namespace", "external_key"],
        unique=True,
        postgresql_where=sa.text("external_key IS NOT NULL"),
    )
    op.create_index("ix_agent_threads_user", "agent_threads", ["namespace", "user_key"])

    op.create_table(
        "agent_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("thread_id", "sequence", name="uq_agent_messages_thread_sequence"),
    )
    op.create_index(
        "ix_agent_messages_thread_created", "agent_messages", ["thread_id", "created_at"]
    )

    op.create_table(
        "memory_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("user_key", sa.Text(), nullable=False),
        sa.Column("memory_key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "namespace", "user_key", "memory_key", name="uq_memory_records_identity"
        ),
    )
    op.create_index("ix_memory_records_subject", "memory_records", ["namespace", "user_key"])
    op.create_index("ix_memory_records_expiry", "memory_records", ["expires_at"])

    op.create_table(
        "runtime_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("agent_version", sa.Text(), nullable=False),
        sa.Column("framework", sa.Text(), nullable=False),
        sa.Column(
            "thread_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_threads.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("request_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('accepted', 'running', 'succeeded', 'failed', 'blocked')",
            name="ck_runtime_runs_status",
        ),
    )
    op.create_index(
        "uq_runtime_runs_agent_idempotency",
        "runtime_runs",
        ["agent_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_runtime_runs_thread_started", "runtime_runs", ["thread_id", "started_at"])
    op.create_index("ix_runtime_runs_agent_started", "runtime_runs", ["agent_id", "started_at"])

    op.create_table(
        "feedback_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runtime_runs.id"),
            nullable=False,
        ),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("feedback_type", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "labels", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "supersedes_feedback_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feedback_records.id"),
            nullable=True,
        ),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_feedback_records_run_created", "feedback_records", ["run_id", "created_at"])
    op.create_index("ix_feedback_records_target", "feedback_records", ["target_type", "target_id"])


def downgrade() -> None:
    """Drop the initial schema in reverse dependency order."""
    op.drop_table("feedback_records")
    op.drop_table("runtime_runs")
    op.drop_table("memory_records")
    op.drop_table("agent_messages")
    op.drop_table("agent_threads")
