"""Add fenced run leases and one active owner per thread.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Require drained workers; preserve terminal responses and all evidence."""
    op.add_column("runtime_runs", sa.Column("owner_token", postgresql.UUID(as_uuid=True)))
    op.add_column("runtime_runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    # Old workers have no ownership proof. Never silently replay their side effects.
    op.execute(
        sa.text(
            "UPDATE runtime_runs SET status = 'failed', error_code = 'legacy_owner_unavailable', "
            "completed_at = clock_timestamp() WHERE status IN ('accepted', 'running')"
        )
    )
    op.create_check_constraint(
        "ck_runtime_runs_active_owner",
        "runtime_runs",
        "status NOT IN ('accepted', 'running') OR "
        "(owner_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
    )
    op.create_index(
        "uq_runtime_runs_active_thread",
        "runtime_runs",
        ["thread_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('accepted', 'running')"),
    )
    op.create_index(
        "ix_runtime_runs_active_expiry",
        "runtime_runs",
        ["lease_expires_at"],
        postgresql_where=sa.text("status IN ('accepted', 'running')"),
    )


def downgrade() -> None:
    """Remove ownership metadata; settled legacy runs are intentionally not resurrected."""
    op.drop_index("ix_runtime_runs_active_expiry", table_name="runtime_runs")
    op.drop_index("uq_runtime_runs_active_thread", table_name="runtime_runs")
    op.drop_constraint("ck_runtime_runs_active_owner", "runtime_runs", type_="check")
    op.drop_column("runtime_runs", "lease_expires_at")
    op.drop_column("runtime_runs", "owner_token")
