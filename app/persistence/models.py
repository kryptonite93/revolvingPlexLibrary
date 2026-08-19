from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.persistence.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EventRecord(Base):
    __tablename__ = "event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class IntegrationInstance(Base):
    __tablename__ = "integration_instance"
    __table_args__ = (UniqueConstraint("kind", "base_url", name="uq_integration_kind_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active_management_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    management_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    credentials_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_from_instance_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=True
    )
    external_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    health_status: Mapped[str] = mapped_column(String(24), nullable=False, default="UNTESTED")
    sanitized_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    full_sync_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dry_run_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ManagedLibrary(Base):
    __tablename__ = "managed_library"
    __table_args__ = (
        UniqueConstraint(
            "plex_integration_id", "external_id", name="uq_managed_library_plex_external"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    plex_integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    media_type: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SourceFreshness(Base):
    __tablename__ = "source_freshness"
    __table_args__ = (
        UniqueConstraint("integration_id", "source_kind", name="uq_freshness_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="NEVER_SYNCED")
    stale_after_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    cursor: Mapped[str | None] = mapped_column(String(240), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sanitized_error: Mapped[str | None] = mapped_column(Text)


class InventoryPolicy(Base):
    __tablename__ = "inventory_policy"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    meaningful_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    meaningful_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    never_watched_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    watched_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    protected_tag_name: Mapped[str] = mapped_column(
        String(120), nullable=False, default="retention-protected"
    )
    tautulli_fresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    torrent_fresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    arr_fresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    overseerr_fresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    plex_fresh_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    sanitized_error: Mapped[str | None] = mapped_column(Text)


class MediaIdentity(Base):
    __tablename__ = "media_identity"
    __table_args__ = (
        UniqueConstraint("media_type", "source_key", name="uq_media_identity_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    media_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(String(240), nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    series_tvdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    season_number: Mapped[int | None] = mapped_column(Integer)
    canonical_title: Mapped[str] = mapped_column(String(360), nullable=False, index=True)
    year: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class MediaLifecycle(Base):
    __tablename__ = "media_lifecycle"
    __table_args__ = (
        UniqueConstraint("integration_id", "arr_item_id", "identity_id", name="uq_lifecycle_arr"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    identity_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_identity.id"), nullable=False, index=True
    )
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    arr_item_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plex_rating_key: Mapped[str | None] = mapped_column(String(160), index=True)
    library_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("managed_library.id"))
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    monitored: Mapped[bool | None] = mapped_column(Boolean)
    first_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    previous_imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_meaningful_watch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    watched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    protection_state: Mapped[str] = mapped_column(String(24), nullable=False, default="UNKNOWN")
    protection_sources: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    decision: Mapped[str] = mapped_column(String(48), nullable=False, default="BLOCKED_UNKNOWN")
    decision_reason: Mapped[str] = mapped_column(
        Text, nullable=False, default="Source data incomplete"
    )
    legacy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_path: Mapped[str | None] = mapped_column(Text)
    current_size: Mapped[int | None] = mapped_column(BigInteger)
    source_download_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    source_version: Mapped[str | None] = mapped_column(String(120))


class MediaFileRevision(Base):
    __tablename__ = "media_file_revision"
    __table_args__ = (UniqueConstraint("lifecycle_id", "arr_file_id", name="uq_file_revision_arr"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lifecycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_lifecycle.id"), nullable=False, index=True
    )
    arr_file_id: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int | None] = mapped_column(BigInteger)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality: Mapped[str | None] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Playback(Base):
    __tablename__ = "playback"
    __table_args__ = (
        UniqueConstraint("integration_id", "external_row_id", name="uq_playback_row"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    external_row_id: Mapped[str] = mapped_column(String(160), nullable=False)
    plex_rating_key: Mapped[str | None] = mapped_column(String(160), index=True)
    parent_rating_key: Mapped[str | None] = mapped_column(String(160), index=True)
    grandparent_rating_key: Mapped[str | None] = mapped_column(String(160), index=True)
    media_type: Mapped[str] = mapped_column(String(24), nullable=False)
    watched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    watched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    meaningful: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_id: Mapped[str | None] = mapped_column(String(160))


class Torrent(Base):
    __tablename__ = "torrent"
    __table_args__ = (UniqueConstraint("integration_id", "info_hash", name="uq_torrent_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    info_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    content_path: Mapped[str | None] = mapped_column(Text)
    save_path: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(240))
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str | None] = mapped_column(String(80))
    size: Mapped[int | None] = mapped_column(BigInteger)
    amount_left: Mapped[int | None] = mapped_column(BigInteger)
    ratio: Mapped[float | None] = mapped_column(Float)
    seeding_seconds: Mapped[int | None] = mapped_column(BigInteger)
    added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TorrentTracker(Base):
    __tablename__ = "torrent_tracker"
    __table_args__ = (UniqueConstraint("torrent_id", "url", name="uq_torrent_tracker_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    torrent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("torrent.id"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    host: Mapped[str] = mapped_column(String(240), nullable=False, index=True)
    tier: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)


class TorrentMediaMapping(Base):
    __tablename__ = "torrent_media_mapping"
    __table_args__ = (UniqueConstraint("torrent_id", "lifecycle_id", name="uq_torrent_media"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    torrent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("torrent.id"), nullable=False, index=True
    )
    lifecycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_lifecycle.id"), nullable=False, index=True
    )
    mapping_source: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TrackerPolicy(Base):
    __tablename__ = "tracker_policy"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    normalized_domain: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    minimum_ratio: Mapped[float | None] = mapped_column(Float)
    minimum_seed_seconds: Mapped[int | None] = mapped_column(BigInteger)
    combination: Mapped[str] = mapped_column(String(24), nullable=False)
    grace_period_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    automatic_deletion_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DryRunProposal(Base):
    __tablename__ = "dry_run_proposal"
    __table_args__ = (UniqueConstraint("lifecycle_id", name="uq_dry_run_lifecycle"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lifecycle_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("media_lifecycle.id"), nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_text: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    eligibility_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class RequesterProfile(Base):
    __tablename__ = "requester_profile"
    __table_args__ = (
        UniqueConstraint(
            "integration_id", "external_id", name="uq_requester_profile_external"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    username: Mapped[str | None] = mapped_column(String(160))
    display_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class RequestRecord(Base):
    __tablename__ = "request_record"
    __table_args__ = (
        UniqueConstraint("integration_id", "external_request_id", name="uq_request_external"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    integration_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("integration_instance.id"), nullable=False, index=True
    )
    external_request_id: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(24), nullable=False)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    requester_id: Mapped[str | None] = mapped_column(String(160))
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ImmutableEventError(RuntimeError):
    pass


@event.listens_for(EventRecord, "before_update")
def _block_event_update(_mapper: object, _connection: object, _target: EventRecord) -> None:
    raise ImmutableEventError("Event history is append-only")


@event.listens_for(EventRecord, "before_delete")
def _block_event_delete(_mapper: object, _connection: object, _target: EventRecord) -> None:
    raise ImmutableEventError("Event history is append-only")
