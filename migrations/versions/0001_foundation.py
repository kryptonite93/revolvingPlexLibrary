"""Create administrator and append-only event tables.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_user",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_table(
        "event",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=160), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=160), nullable=True),
        sa.Column("actor_type", sa.String(length=40), nullable=False),
        sa.Column("actor_id", sa.String(length=160), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=36), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("event_type", "entity_type", "entity_id", "occurred_at", "correlation_id"):
        op.create_index(f"ix_event_{column}", "event", [column], unique=False)

    op.execute(
        """
        CREATE TRIGGER event_prevent_update
        BEFORE UPDATE ON event
        BEGIN
            SELECT RAISE(ABORT, 'event history is append-only');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER event_prevent_delete
        BEFORE DELETE ON event
        BEGIN
            SELECT RAISE(ABORT, 'event history is append-only');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS event_prevent_delete")
    op.execute("DROP TRIGGER IF EXISTS event_prevent_update")
    op.drop_table("event")
    op.drop_table("admin_user")
