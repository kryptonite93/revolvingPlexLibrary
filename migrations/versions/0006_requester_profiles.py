"""Store Overseerr requester profiles and protection selections.

Revision ID: 0006_requester_profiles
Revises: 0005_media_monitoring_state
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_requester_profiles"
down_revision = "0005_media_monitoring_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requester_profile",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("external_id", sa.String(160), nullable=False),
        sa.Column("username", sa.String(160)),
        sa.Column("display_name", sa.String(200)),
        sa.Column("email", sa.String(320)),
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.UniqueConstraint(
            "integration_id", "external_id", name="uq_requester_profile_external"
        ),
    )
    op.create_index(
        "ix_requester_profile_integration_id", "requester_profile", ["integration_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_requester_profile_integration_id", table_name="requester_profile")
    op.drop_table("requester_profile")
