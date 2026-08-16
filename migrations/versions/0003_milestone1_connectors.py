"""Add connector readiness and Plex library selection.

Revision ID: 0003_milestone1_connectors
Revises: 0002_integration_registry
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_milestone1_connectors"
down_revision = "0002_integration_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("integration_instance") as batch:
        batch.add_column(sa.Column("full_sync_completed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("dry_run_evaluated_at", sa.DateTime(timezone=True)))

    op.create_table(
        "managed_library",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plex_integration_id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("media_type", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plex_integration_id"], ["integration_instance.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plex_integration_id", "external_id", name="uq_managed_library_plex_external"
        ),
    )
    op.create_index(
        "ix_managed_library_plex_integration_id",
        "managed_library",
        ["plex_integration_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_managed_library_plex_integration_id", table_name="managed_library")
    op.drop_table("managed_library")
    with op.batch_alter_table("integration_instance") as batch:
        batch.drop_column("dry_run_evaluated_at")
        batch.drop_column("full_sync_completed_at")
