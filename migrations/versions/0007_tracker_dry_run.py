"""Add tracker policies and fail-closed dry-run proposals.

Revision ID: 0007_tracker_dry_run
Revises: 0006_requester_profiles
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_tracker_dry_run"
down_revision = "0006_requester_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("torrent") as batch_op:
        batch_op.add_column(sa.Column("seeding_seconds", sa.BigInteger()))
    op.create_table(
        "tracker_policy",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("normalized_domain", sa.String(240), nullable=False, unique=True),
        sa.Column("minimum_ratio", sa.Float()),
        sa.Column("minimum_seed_seconds", sa.BigInteger()),
        sa.Column("combination", sa.String(24), nullable=False),
        sa.Column("grace_period_seconds", sa.BigInteger(), nullable=False),
        sa.Column("automatic_deletion_allowed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "dry_run_proposal",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lifecycle_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("estimated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("eligibility_snapshot", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["media_lifecycle.id"]),
        sa.UniqueConstraint("lifecycle_id", name="uq_dry_run_lifecycle"),
    )
    op.create_index("ix_dry_run_proposal_lifecycle_id", "dry_run_proposal", ["lifecycle_id"])
    op.create_index("ix_dry_run_proposal_state", "dry_run_proposal", ["state"])
    op.create_index("ix_dry_run_proposal_evaluated_at", "dry_run_proposal", ["evaluated_at"])


def downgrade() -> None:
    op.drop_table("dry_run_proposal")
    op.drop_table("tracker_policy")
    with op.batch_alter_table("torrent") as batch_op:
        batch_op.drop_column("seeding_seconds")
