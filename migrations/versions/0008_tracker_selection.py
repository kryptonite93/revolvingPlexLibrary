"""Add explicit tracker-rule selection.

Revision ID: 0008_tracker_selection
Revises: 0007_tracker_dry_run
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_tracker_selection"
down_revision = "0007_tracker_dry_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tracker_policy") as batch_op:
        batch_op.add_column(
            sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("tracker_policy") as batch_op:
        batch_op.drop_column("selected")
