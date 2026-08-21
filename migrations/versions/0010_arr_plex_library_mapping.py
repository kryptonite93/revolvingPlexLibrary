"""Pair Arr integrations with their Plex libraries.

Revision ID: 0010_arr_plex_library_mapping
Revises: 0009_manual_movie_execution
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_arr_plex_library_mapping"
down_revision = "0009_manual_movie_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integration_library_mapping",
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("library_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="AUTO"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.ForeignKeyConstraint(["library_id"], ["managed_library.id"]),
        sa.PrimaryKeyConstraint("integration_id"),
        sa.UniqueConstraint("library_id", name="uq_integration_library_mapping_library"),
    )
    op.create_index(
        "ix_integration_library_mapping_library_id",
        "integration_library_mapping",
        ["library_id"],
    )


def downgrade() -> None:
    op.drop_table("integration_library_mapping")
