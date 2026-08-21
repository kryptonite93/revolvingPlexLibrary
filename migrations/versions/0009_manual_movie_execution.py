"""Add rollout policy and persisted manual movie deletion jobs.

Revision ID: 0009_manual_movie_execution
Revises: 0008_tracker_selection
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_manual_movie_execution"
down_revision = "0008_tracker_selection"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rollout_policy",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "mode",
            sa.String(32),
            nullable=False,
            server_default="INVENTORY_ONLY",
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "deletion_job",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lifecycle_id", sa.String(36), nullable=False),
        sa.Column("proposal_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("current_step", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("requested_by_admin_id", sa.String(36), nullable=False),
        sa.Column("approved_by_admin_id", sa.String(36)),
        sa.Column("approval_snapshot", sa.JSON(), nullable=False),
        sa.Column("execution_snapshot", sa.JSON(), nullable=False),
        sa.Column("external_state", sa.JSON(), nullable=False),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("last_error", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["media_lifecycle.id"]),
        sa.ForeignKeyConstraint(["requested_by_admin_id"], ["admin_user.id"]),
        sa.ForeignKeyConstraint(["approved_by_admin_id"], ["admin_user.id"]),
        sa.UniqueConstraint("lifecycle_id", name="uq_deletion_job_lifecycle"),
    )
    op.create_index("ix_deletion_job_lifecycle_id", "deletion_job", ["lifecycle_id"])
    op.create_index("ix_deletion_job_state", "deletion_job", ["state"])
    op.create_index("ix_deletion_job_correlation_id", "deletion_job", ["correlation_id"])
    op.create_index("ix_deletion_job_created_at", "deletion_job", ["created_at"])


def downgrade() -> None:
    op.drop_table("deletion_job")
    op.drop_table("rollout_policy")
