"""Add normalized read-only inventory and sync state.

Revision ID: 0004_read_only_inventory
Revises: 0003_milestone1_connectors
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_read_only_inventory"
down_revision = "0003_milestone1_connectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_policy",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("meaningful_minutes", sa.Integer(), nullable=False),
        sa.Column("meaningful_percent", sa.Integer(), nullable=False),
        sa.Column("never_watched_weeks", sa.Integer(), nullable=False),
        sa.Column("watched_weeks", sa.Integer(), nullable=False),
        sa.Column("protected_tag_name", sa.String(120), nullable=False),
        sa.Column("tautulli_fresh_minutes", sa.Integer(), nullable=False),
        sa.Column("torrent_fresh_minutes", sa.Integer(), nullable=False),
        sa.Column("arr_fresh_minutes", sa.Integer(), nullable=False),
        sa.Column("overseerr_fresh_minutes", sa.Integer(), nullable=False),
        sa.Column("plex_fresh_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "source_freshness",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("source_kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("stale_after_seconds", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.String(240)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("sanitized_error", sa.Text()),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.UniqueConstraint("integration_id", "source_kind", name="uq_freshness_source"),
    )
    op.create_index("ix_source_freshness_integration_id", "source_freshness", ["integration_id"])
    op.create_table(
        "sync_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("sanitized_error", sa.Text()),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
    )
    op.create_index("ix_sync_run_integration_id", "sync_run", ["integration_id"])
    op.create_table(
        "media_identity",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("media_type", sa.String(16), nullable=False),
        sa.Column("source_key", sa.String(240), nullable=False),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("tvdb_id", sa.Integer()),
        sa.Column("series_tvdb_id", sa.Integer()),
        sa.Column("season_number", sa.Integer()),
        sa.Column("canonical_title", sa.String(360), nullable=False),
        sa.Column("year", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("media_type", "source_key", name="uq_media_identity_source"),
    )
    for column in ("media_type", "tmdb_id", "tvdb_id", "series_tvdb_id", "canonical_title"):
        op.create_index(f"ix_media_identity_{column}", "media_identity", [column])
    op.create_table(
        "media_lifecycle",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("identity_id", sa.String(36), nullable=False),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("arr_item_id", sa.Integer(), nullable=False),
        sa.Column("plex_rating_key", sa.String(160)),
        sa.Column("library_id", sa.String(36)),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("first_imported_at", sa.DateTime(timezone=True)),
        sa.Column("previous_imported_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("last_meaningful_watch_at", sa.DateTime(timezone=True)),
        sa.Column("retention_deadline", sa.DateTime(timezone=True)),
        sa.Column("watched", sa.Boolean(), nullable=False),
        sa.Column("protection_state", sa.String(24), nullable=False),
        sa.Column("protection_sources", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(48), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.Column("current_path", sa.Text()),
        sa.Column("current_size", sa.BigInteger()),
        sa.Column("source_download_ids", sa.JSON(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.String(120)),
        sa.ForeignKeyConstraint(["identity_id"], ["media_identity.id"]),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.ForeignKeyConstraint(["library_id"], ["managed_library.id"]),
        sa.UniqueConstraint(
            "integration_id", "arr_item_id", "identity_id", name="uq_lifecycle_arr"
        ),
    )
    for column in (
        "identity_id",
        "integration_id",
        "plex_rating_key",
        "first_imported_at",
        "retention_deadline",
    ):
        op.create_index(f"ix_media_lifecycle_{column}", "media_lifecycle", [column])
    op.create_table(
        "media_file_revision",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("lifecycle_id", sa.String(36), nullable=False),
        sa.Column("arr_file_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.Text()),
        sa.Column("size", sa.BigInteger()),
        sa.Column("imported_at", sa.DateTime(timezone=True)),
        sa.Column("quality", sa.String(160)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["media_lifecycle.id"]),
        sa.UniqueConstraint("lifecycle_id", "arr_file_id", name="uq_file_revision_arr"),
    )
    op.create_index("ix_media_file_revision_lifecycle_id", "media_file_revision", ["lifecycle_id"])
    op.create_table(
        "playback",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("external_row_id", sa.String(160), nullable=False),
        sa.Column("plex_rating_key", sa.String(160)),
        sa.Column("parent_rating_key", sa.String(160)),
        sa.Column("grandparent_rating_key", sa.String(160)),
        sa.Column("media_type", sa.String(24), nullable=False),
        sa.Column("watched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("progress_percent", sa.Float(), nullable=False),
        sa.Column("watched", sa.Boolean(), nullable=False),
        sa.Column("meaningful", sa.Boolean(), nullable=False),
        sa.Column("user_id", sa.String(160)),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.UniqueConstraint("integration_id", "external_row_id", name="uq_playback_row"),
    )
    for column in (
        "integration_id",
        "plex_rating_key",
        "parent_rating_key",
        "grandparent_rating_key",
        "watched_at",
    ):
        op.create_index(f"ix_playback_{column}", "playback", [column])
    op.create_table(
        "torrent",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("info_hash", sa.String(80), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("content_path", sa.Text()),
        sa.Column("save_path", sa.Text()),
        sa.Column("category", sa.String(240)),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(80)),
        sa.Column("size", sa.BigInteger()),
        sa.Column("amount_left", sa.BigInteger()),
        sa.Column("ratio", sa.Float()),
        sa.Column("added_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_activity_at", sa.DateTime(timezone=True)),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.UniqueConstraint("integration_id", "info_hash", name="uq_torrent_hash"),
    )
    op.create_index("ix_torrent_integration_id", "torrent", ["integration_id"])
    op.create_index("ix_torrent_info_hash", "torrent", ["info_hash"])
    op.create_table(
        "torrent_tracker",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("torrent_id", sa.String(36), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("host", sa.String(240), nullable=False),
        sa.Column("tier", sa.Integer()),
        sa.Column("status", sa.Integer()),
        sa.Column("message", sa.Text()),
        sa.ForeignKeyConstraint(["torrent_id"], ["torrent.id"]),
        sa.UniqueConstraint("torrent_id", "url", name="uq_torrent_tracker_url"),
    )
    op.create_index("ix_torrent_tracker_torrent_id", "torrent_tracker", ["torrent_id"])
    op.create_index("ix_torrent_tracker_host", "torrent_tracker", ["host"])
    op.create_table(
        "torrent_media_mapping",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("torrent_id", sa.String(36), nullable=False),
        sa.Column("lifecycle_id", sa.String(36), nullable=False),
        sa.Column("mapping_source", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["torrent_id"], ["torrent.id"]),
        sa.ForeignKeyConstraint(["lifecycle_id"], ["media_lifecycle.id"]),
        sa.UniqueConstraint("torrent_id", "lifecycle_id", name="uq_torrent_media"),
    )
    op.create_index("ix_torrent_media_mapping_torrent_id", "torrent_media_mapping", ["torrent_id"])
    op.create_index(
        "ix_torrent_media_mapping_lifecycle_id", "torrent_media_mapping", ["lifecycle_id"]
    )
    op.create_table(
        "request_record",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("integration_id", sa.String(36), nullable=False),
        sa.Column("external_request_id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(24), nullable=False),
        sa.Column("tmdb_id", sa.Integer()),
        sa.Column("tvdb_id", sa.Integer()),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("requester_id", sa.String(160)),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("present", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integration_instance.id"]),
        sa.UniqueConstraint("integration_id", "external_request_id", name="uq_request_external"),
    )
    op.create_index("ix_request_record_integration_id", "request_record", ["integration_id"])
    op.create_index("ix_request_record_tmdb_id", "request_record", ["tmdb_id"])
    op.create_index("ix_request_record_tvdb_id", "request_record", ["tvdb_id"])


def downgrade() -> None:
    for table in (
        "request_record",
        "torrent_media_mapping",
        "torrent_tracker",
        "torrent",
        "playback",
        "media_file_revision",
        "media_lifecycle",
        "media_identity",
        "sync_run",
        "source_freshness",
        "inventory_policy",
    ):
        op.drop_table(table)
