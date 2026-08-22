"""Add playback usernames and manual management batches.

Revision ID: 0011_manual_management
Revises: 0010_arr_plex_library_mapping
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_manual_management"
down_revision = "0010_arr_plex_library_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("playback", sa.Column("user_name", sa.String(200)))
    op.create_table(
        "manual_deletion_batch",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("correlation_id", sa.String(36), nullable=False, unique=True),
        sa.Column("requester_profile_id", sa.String(36), nullable=False),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("requested_by_admin_id", sa.String(36), nullable=False),
        sa.Column("add_import_exclusion", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("completed_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["requester_profile_id"], ["requester_profile.id"]),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.ForeignKeyConstraint(["requested_by_admin_id"], ["admin_user.id"]),
    )
    op.create_index(
        "ix_manual_deletion_batch_correlation_id",
        "manual_deletion_batch",
        ["correlation_id"],
    )
    op.create_index(
        "ix_manual_deletion_batch_requester_profile_id",
        "manual_deletion_batch",
        ["requester_profile_id"],
    )
    op.create_index(
        "ix_manual_deletion_batch_integration_id",
        "manual_deletion_batch",
        ["integration_id"],
    )
    op.create_index("ix_manual_deletion_batch_state", "manual_deletion_batch", ["state"])
    op.create_index(
        "ix_manual_deletion_batch_created_at", "manual_deletion_batch", ["created_at"]
    )
    op.create_table(
        "manual_deletion_item",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("lifecycle_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("current_step", sa.String(64), nullable=False),
        sa.Column("external_state", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["batch_id"], ["manual_deletion_batch.id"]),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["media_lifecycle.id"]),
        sa.UniqueConstraint(
            "batch_id", "lifecycle_id", name="uq_manual_deletion_batch_lifecycle"
        ),
    )
    op.create_index("ix_manual_deletion_item_batch_id", "manual_deletion_item", ["batch_id"])
    op.create_index(
        "ix_manual_deletion_item_lifecycle_id", "manual_deletion_item", ["lifecycle_id"]
    )
    op.create_index("ix_manual_deletion_item_state", "manual_deletion_item", ["state"])


def downgrade() -> None:
    op.drop_table("manual_deletion_item")
    op.drop_table("manual_deletion_batch")
    op.drop_column("playback", "user_name")
