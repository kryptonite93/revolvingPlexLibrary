"""Track Arr monitoring state for lifecycle presentation.

Revision ID: 0005_media_monitoring_state
Revises: 0004_read_only_inventory
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_media_monitoring_state"
down_revision = "0004_read_only_inventory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_lifecycle") as batch:
        batch.add_column(sa.Column("monitored", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("media_lifecycle") as batch:
        batch.drop_column("monitored")
