"""Create the encrypted integration registry.

Revision ID: 0002_integration_registry
Revises: 0001_foundation
Create Date: 2026-08-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_integration_registry"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_instance",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "active_management_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("management_mode", sa.String(length=20), nullable=True),
        sa.Column("credentials_encrypted", sa.Text(), nullable=False),
        sa.Column("discovered_from_instance_id", sa.String(length=36), nullable=True),
        sa.Column("external_id", sa.String(length=160), nullable=True),
        sa.Column("health_status", sa.String(length=24), nullable=False, server_default="UNTESTED"),
        sa.Column("sanitized_error", sa.Text(), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["discovered_from_instance_id"], ["integration_instance.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "base_url", name="uq_integration_kind_url"),
    )
    op.create_index("ix_integration_instance_kind", "integration_instance", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_integration_instance_kind", table_name="integration_instance")
    op.drop_table("integration_instance")
