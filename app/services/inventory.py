from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.arr import ArrAdapter
from app.integrations.overseerr import OverseerrAdapter
from app.integrations.plex import PlexAdapter
from app.integrations.qbittorrent import QBittorrentAdapter
from app.integrations.tautulli import TautulliAdapter
from app.persistence.models import (
    IntegrationInstance,
    InventoryPolicy,
    ManagedLibrary,
    MediaFileRevision,
    MediaIdentity,
    MediaLifecycle,
    Playback,
    RequesterProfile,
    RequestRecord,
    SourceFreshness,
    SyncRun,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
    utc_now,
)
from app.security.credentials import CredentialCipher
from app.security.redaction import redact


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        timestamp = int(value)
        return datetime.fromtimestamp(timestamp, UTC) if timestamp > 0 else None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def meaningful_playback(
    duration_seconds: int,
    progress_percent: float,
    watched: bool,
    *,
    minutes: int = 10,
    percent: int = 10,
) -> bool:
    return watched or duration_seconds >= minutes * 60 or progress_percent >= percent


def retention_deadline(
    media_type: str,
    first_imported_at: datetime | None,
    last_watch_at: datetime | None,
    *,
    never_watched_weeks: int = 16,
    watched_weeks: int = 8,
) -> datetime | None:
    if first_imported_at is None:
        return None
    if last_watch_at and as_utc(last_watch_at) >= as_utc(first_imported_at):
        return last_watch_at + timedelta(weeks=watched_weeks)
    return first_imported_at + timedelta(weeks=never_watched_weeks)


def _default_inventory_policy() -> InventoryPolicy:
    return InventoryPolicy(
        id="default",
        meaningful_minutes=10,
        meaningful_percent=10,
        never_watched_weeks=16,
        watched_weeks=8,
        protected_tag_name="retention-protected",
        tautulli_fresh_minutes=15,
        torrent_fresh_minutes=15,
        arr_fresh_minutes=60,
        overseerr_fresh_minutes=60,
        plex_fresh_minutes=60,
    )


def get_inventory_policy(session: Session) -> InventoryPolicy:
    policy = session.get(InventoryPolicy, "default")
    if policy is None:
        policy = _default_inventory_policy()
        session.add(policy)
        session.flush()
    return policy


def _freshness(
    session: Session, integration: IntegrationInstance, policy: InventoryPolicy
) -> SourceFreshness:
    minutes = {
        "TAUTULLI": policy.tautulli_fresh_minutes,
        "QBITTORRENT": policy.torrent_fresh_minutes,
        "RADARR": policy.arr_fresh_minutes,
        "SONARR": policy.arr_fresh_minutes,
        "OVERSEERR": policy.overseerr_fresh_minutes,
        "PLEX": policy.plex_fresh_minutes,
    }[integration.kind]
    row = session.scalar(
        select(SourceFreshness).where(
            SourceFreshness.integration_id == integration.id,
            SourceFreshness.source_kind == integration.kind,
        )
    )
    if row is None:
        row = SourceFreshness(
            integration_id=integration.id,
            source_kind=integration.kind,
            stale_after_seconds=minutes * 60,
        )
        session.add(row)
    else:
        row.stale_after_seconds = minutes * 60
    return row


def _history_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        records = payload.get("records", [])
        return records if isinstance(records, list) else []
    return []


def _download_ids(history: list[dict[str, Any]], *, item_id: int, series: bool) -> list[str]:
    key = "seriesId" if series else "movieId"
    values = {
        str(record.get("downloadId", "")).lower()
        for record in history
        if record.get(key) == item_id and record.get("downloadId")
    }
    return sorted(values)


def _oldest_history_import(
    history: list[dict[str, Any]],
    *,
    item_key: str,
    item_ids: set[int],
) -> datetime | None:
    dates = [
        parse_datetime(record.get("date"))
        for record in history
        if record.get(item_key) in item_ids
        and "import" in str(record.get("eventType") or "").casefold()
    ]
    valid_dates = [date for date in dates if date is not None]
    return min(valid_dates) if valid_dates else None


def _protection(integration: IntegrationInstance) -> tuple[str, list[str]]:
    if integration.management_mode == "PROTECTED":
        return "PROTECTED", ["INSTANCE_MODE"]
    if integration.management_mode == "IGNORED":
        return "PROTECTED", ["INSTANCE_IGNORED"]
    return "UNPROTECTED", []


def _item_protection(
    integration: IntegrationInstance,
    item: dict[str, Any],
    protected_tag_ids: set[int],
) -> tuple[str, list[str]]:
    state, sources = _protection(integration)
    item_tags = {int(value) for value in item.get("tags", [])}
    if item_tags & protected_tag_ids:
        state = "PROTECTED"
        sources = [*sources, "ARR_TAG"]
    return state, sources


def _apply_arr_item_protection(
    lifecycle: MediaLifecycle,
    integration: IntegrationInstance,
    item: dict[str, Any],
    protected_tag_ids: set[int],
) -> None:
    state, sources = _item_protection(integration, item, protected_tag_ids)
    arr_sources = {"INSTANCE_MODE", "INSTANCE_IGNORED", "ARR_TAG"}
    preserved = [
        source for source in (lifecycle.protection_sources or []) if source not in arr_sources
    ]
    sources.extend(source for source in preserved if source not in sources)
    lifecycle.protection_sources = sources
    lifecycle.protection_state = "PROTECTED" if sources else state


def _set_decision(
    lifecycle: MediaLifecycle,
    *,
    all_required_sources_fresh: bool = False,
    stale_sources: tuple[str, ...] = (),
) -> None:
    if lifecycle.state == "MISSING":
        lifecycle.decision = "NOT_IN_LIBRARY"
        lifecycle.decision_reason = "No downloaded files are present"
    elif lifecycle.protection_state == "PROTECTED":
        lifecycle.decision = "KEEP_PROTECTED"
        lifecycle.decision_reason = "Protected by " + ", ".join(lifecycle.protection_sources)
    elif not all_required_sources_fresh:
        lifecycle.decision = "BLOCKED_STALE"
        if stale_sources:
            source_list = (
                stale_sources[0]
                if len(stale_sources) == 1
                else f"{', '.join(stale_sources[:-1])} and {stale_sources[-1]}"
            )
            lifecycle.decision_reason = f"Waiting for fresh data from {source_list}"
        else:
            lifecycle.decision_reason = "No required evidence sources are fresh"
    elif lifecycle.first_imported_at is None or lifecycle.retention_deadline is None:
        lifecycle.decision = "BLOCKED_UNKNOWN"
        lifecycle.decision_reason = "Original import date is unavailable"
    elif as_utc(lifecycle.retention_deadline) > utc_now():
        lifecycle.decision = "KEEP_RETAINED"
        lifecycle.decision_reason = "Retention deadline has not passed"
    else:
        lifecycle.decision = "REVIEW_ELIGIBLE"
        lifecycle.decision_reason = "Retention elapsed; future dry-run must revalidate every source"


def _identity(
    session: Session,
    *,
    media_type: str,
    source_key: str,
    title: str,
    tmdb_id: int | None = None,
    tvdb_id: int | None = None,
    series_tvdb_id: int | None = None,
    season_number: int | None = None,
    year: int | None = None,
) -> MediaIdentity:
    item = session.scalar(
        select(MediaIdentity).where(
            MediaIdentity.media_type == media_type, MediaIdentity.source_key == source_key
        )
    )
    if item is None:
        item = MediaIdentity(media_type=media_type, source_key=source_key, canonical_title=title)
        session.add(item)
    item.canonical_title = title
    item.tmdb_id = tmdb_id
    item.tvdb_id = tvdb_id
    item.series_tvdb_id = series_tvdb_id
    item.season_number = season_number
    item.year = year
    return item


def _lifecycle(
    session: Session, integration: IntegrationInstance, identity: MediaIdentity, arr_item_id: int
) -> MediaLifecycle:
    session.flush()
    item = session.scalar(
        select(MediaLifecycle).where(
            MediaLifecycle.integration_id == integration.id,
            MediaLifecycle.identity_id == identity.id,
            MediaLifecycle.arr_item_id == arr_item_id,
        )
    )
    if item is None:
        item = MediaLifecycle(
            identity_id=identity.id,
            integration_id=integration.id,
            arr_item_id=arr_item_id,
        )
        session.add(item)
    return item


def _upsert_file(
    session: Session, lifecycle: MediaLifecycle, payload: dict[str, Any], now: datetime
) -> None:
    file_id = payload.get("id")
    if file_id is None:
        return
    session.flush()
    revision = session.scalar(
        select(MediaFileRevision).where(
            MediaFileRevision.lifecycle_id == lifecycle.id,
            MediaFileRevision.arr_file_id == int(file_id),
        )
    )
    if revision is None:
        revision = MediaFileRevision(lifecycle_id=lifecycle.id, arr_file_id=int(file_id))
        session.add(revision)
    revision.path = payload.get("path") or payload.get("relativePath")
    revision.size = payload.get("size")
    revision.imported_at = parse_datetime(payload.get("dateAdded"))
    quality = payload.get("quality", {})
    revision.quality = (
        str(quality.get("quality", {}).get("name", "")) if isinstance(quality, dict) else None
    )
    revision.active = True
    revision.last_seen_at = now


def sync_arr(
    session: Session,
    integration: IntegrationInstance,
    credentials: dict[str, str],
    policy: InventoryPolicy,
    payload: dict[str, Any] | None = None,
) -> dict[str, int]:
    if payload is None:
        payload = ArrAdapter(integration.base_url, credentials["api_key"]).inventory(
            integration.kind
        )
    items = payload.get("items", []) if isinstance(payload.get("items"), list) else []
    files = payload.get("files", []) if isinstance(payload.get("files"), list) else []
    episodes = payload.get("episodes", []) if isinstance(payload.get("episodes"), list) else []
    history = _history_records(payload.get("history"))
    tags = payload.get("tags", []) if isinstance(payload.get("tags"), list) else []
    protected_tag_ids = {
        int(tag["id"])
        for tag in tags
        if tag.get("id") is not None
        and str(tag.get("label") or "").casefold() == policy.protected_tag_name.casefold()
    }
    now = utc_now()
    session.query(MediaLifecycle).filter(MediaLifecycle.integration_id == integration.id).update(
        {"state": "MISSING"}
    )
    lifecycle_ids = select(MediaLifecycle.id).where(MediaLifecycle.integration_id == integration.id)
    session.query(MediaFileRevision).filter(
        MediaFileRevision.lifecycle_id.in_(lifecycle_ids)
    ).update({"active": False}, synchronize_session=False)
    lifecycle_count = 0
    file_count = 0
    if integration.kind == "RADARR":
        files_by_movie = {
            item.get("movieId"): item for item in files if item.get("movieId") is not None
        }
        for item in items:
            arr_id = int(item["id"])
            tmdb_id = item.get("tmdbId")
            identity = _identity(
                session,
                media_type="MOVIE",
                source_key=f"tmdb:{tmdb_id}" if tmdb_id else f"radarr:{integration.id}:{arr_id}",
                title=str(item.get("title") or "Untitled movie"),
                tmdb_id=tmdb_id,
                year=item.get("year"),
            )
            lifecycle = _lifecycle(session, integration, identity, arr_id)
            movie_file = item.get("movieFile") or files_by_movie.get(arr_id)
            imported = (
                parse_datetime(movie_file.get("dateAdded"))
                if isinstance(movie_file, dict)
                else None
            )
            history_imported = _oldest_history_import(
                history, item_key="movieId", item_ids={arr_id}
            )
            if history_imported and (imported is None or history_imported < imported):
                imported = history_imported
            lifecycle.state = "ACTIVE" if item.get("hasFile") else "MISSING"
            lifecycle.monitored = (
                bool(item["monitored"]) if item.get("monitored") is not None else None
            )
            lifecycle.first_imported_at = lifecycle.first_imported_at or imported
            lifecycle.current_path = (
                movie_file.get("path") if isinstance(movie_file, dict) else item.get("path")
            )
            lifecycle.current_size = movie_file.get("size") if isinstance(movie_file, dict) else 0
            lifecycle.source_download_ids = _download_ids(history, item_id=arr_id, series=False)
            _apply_arr_item_protection(lifecycle, integration, item, protected_tag_ids)
            lifecycle.retention_deadline = retention_deadline(
                "MOVIE",
                lifecycle.first_imported_at,
                lifecycle.last_meaningful_watch_at,
                never_watched_weeks=policy.never_watched_weeks,
                watched_weeks=policy.watched_weeks,
            )
            lifecycle.last_synced_at = now
            _set_decision(lifecycle)
            if isinstance(movie_file, dict):
                _upsert_file(session, lifecycle, movie_file, now)
                file_count += 1
            lifecycle_count += 1
    else:
        files_by_series: dict[int, list[dict[str, Any]]] = {}
        episodes_by_series: dict[int, list[dict[str, Any]]] = {}
        for item in files:
            if item.get("seriesId") is not None:
                files_by_series.setdefault(int(item["seriesId"]), []).append(item)
        for item in episodes:
            if item.get("seriesId") is not None:
                episodes_by_series.setdefault(int(item["seriesId"]), []).append(item)
        for series in items:
            series_id = int(series["id"])
            tvdb_id = series.get("tvdbId")
            series_files = files_by_series.get(series_id, [])
            for season in series.get("seasons", []):
                number = int(season.get("seasonNumber", 0))
                season_files = [f for f in series_files if int(f.get("seasonNumber", -1)) == number]
                identity = _identity(
                    session,
                    media_type="SEASON",
                    source_key=f"tvdb:{tvdb_id}:season:{number}"
                    if tvdb_id
                    else f"sonarr:{integration.id}:{series_id}:{number}",
                    title=f"{series.get('title') or 'Untitled series'} · Season {number}",
                    tvdb_id=tvdb_id,
                    series_tvdb_id=tvdb_id,
                    season_number=number,
                    year=series.get("year"),
                )
                lifecycle = _lifecycle(session, integration, identity, series_id)
                imports = [parse_datetime(f.get("dateAdded")) for f in season_files]
                valid_imports = [date for date in imports if date]
                episode_ids = {
                    int(episode["id"])
                    for episode in episodes_by_series.get(series_id, [])
                    if episode.get("id") is not None
                    and int(episode.get("seasonNumber", -1)) == number
                }
                history_imported = _oldest_history_import(
                    history, item_key="episodeId", item_ids=episode_ids
                )
                if history_imported:
                    valid_imports.append(history_imported)
                lifecycle.state = "ACTIVE" if season_files else "MISSING"
                lifecycle.monitored = (
                    bool(season["monitored"]) if season.get("monitored") is not None else None
                )
                lifecycle.first_imported_at = lifecycle.first_imported_at or (
                    min(valid_imports) if valid_imports else None
                )
                lifecycle.current_path = series.get("path")
                lifecycle.current_size = sum(int(f.get("size") or 0) for f in season_files)
                lifecycle.source_download_ids = _download_ids(
                    history, item_id=series_id, series=True
                )
                _apply_arr_item_protection(
                    lifecycle, integration, series, protected_tag_ids
                )
                lifecycle.retention_deadline = retention_deadline(
                    "SEASON",
                    lifecycle.first_imported_at,
                    lifecycle.last_meaningful_watch_at,
                    never_watched_weeks=policy.never_watched_weeks,
                    watched_weeks=policy.watched_weeks,
                )
                lifecycle.last_synced_at = now
                _set_decision(lifecycle)
                for file_payload in season_files:
                    _upsert_file(session, lifecycle, file_payload, now)
                    file_count += 1
                lifecycle_count += 1
    _map_torrents(session)
    return {"lifecycles": lifecycle_count, "file_revisions": file_count}


def fetch_tautulli_history(
    integration: IntegrationInstance,
    credentials: dict[str, str],
    *,
    start_offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    adapter = TautulliAdapter(integration.base_url, credentials["api_key"])
    start = start_offset
    records: list[dict[str, Any]] = []
    while True:
        payload = adapter.history(start=start, length=1000)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            raise ValueError("Tautulli returned invalid history data")
        records.extend(item for item in rows if isinstance(item, dict))
        records_total = int(payload.get("recordsTotal") or len(rows))
        start += len(rows)
        if not rows or start >= records_total:
            break
    return records, start


def sync_tautulli(
    session: Session,
    integration: IntegrationInstance,
    credentials: dict[str, str],
    policy: InventoryPolicy,
    *,
    start_offset: int = 0,
    fetched_history: tuple[list[dict[str, Any]], int] | None = None,
) -> dict[str, int]:
    rows, final_offset = fetched_history or fetch_tautulli_history(
        integration,
        credentials,
        start_offset=start_offset,
    )
    count = 0
    for row in rows:
        external_id = str(row.get("row_id") or row.get("id") or "")
        if not external_id:
            continue
        playback = session.scalar(
            select(Playback).where(
                Playback.integration_id == integration.id,
                Playback.external_row_id == external_id,
            )
        )
        if playback is None:
            playback = Playback(integration_id=integration.id, external_row_id=external_id)
            session.add(playback)
        duration = int(
            row.get("play_duration")
            or max(0, int(row.get("stopped") or 0) - int(row.get("started") or 0))
        )
        media_duration = int(row.get("duration") or 0) / 1000
        progress = min(100.0, duration / media_duration * 100) if media_duration else 0.0
        watched = bool(row.get("watched_status") or row.get("watched"))
        playback.plex_rating_key = str(row.get("rating_key") or "") or None
        playback.parent_rating_key = str(row.get("parent_rating_key") or "") or None
        playback.grandparent_rating_key = str(row.get("grandparent_rating_key") or "") or None
        playback.media_type = str(row.get("media_type") or "unknown")
        playback.watched_at = parse_datetime(row.get("date") or row.get("started")) or utc_now()
        playback.duration_seconds = duration
        playback.progress_percent = progress
        playback.watched = watched
        playback.meaningful = meaningful_playback(
            duration,
            progress,
            watched,
            minutes=policy.meaningful_minutes,
            percent=policy.meaningful_percent,
        )
        playback.user_id = str(row.get("user_id") or "") or None
        count += 1
    _apply_playback(session, policy)
    return {"new_playbacks": count, "cursor": final_offset}


def _apply_playback(session: Session, policy: InventoryPolicy) -> None:
    session.flush()
    records = session.execute(
        select(MediaLifecycle, MediaIdentity).join(
            MediaIdentity, MediaLifecycle.identity_id == MediaIdentity.id
        )
    ).all()
    direct_watches: dict[str, datetime | None] = {}
    for lifecycle, _identity_item in records:
        if not lifecycle.plex_rating_key:
            direct_watches[lifecycle.id] = None
            continue
        direct_watches[lifecycle.id] = session.scalar(
            select(func.max(Playback.watched_at)).where(
                Playback.meaningful.is_(True),
                (Playback.plex_rating_key == lifecycle.plex_rating_key)
                | (Playback.parent_rating_key == lifecycle.plex_rating_key),
            )
        )
    effective_watches: dict[str, datetime | None] = {}
    for lifecycle, _identity_item in records:
        direct = direct_watches[lifecycle.id]
        imported_at = lifecycle.first_imported_at
        effective_watches[lifecycle.id] = (
            direct
            if lifecycle.state == "ACTIVE"
            and direct
            and imported_at
            and as_utc(direct) >= as_utc(imported_at)
            else None
        )
    series_groups: dict[int, list[tuple[MediaLifecycle, MediaIdentity]]] = {}
    for lifecycle, identity in records:
        if identity.media_type == "SEASON" and identity.series_tvdb_id is not None:
            series_groups.setdefault(identity.series_tvdb_id, []).append((lifecycle, identity))
    for seasons in series_groups.values():
        latest_prior: datetime | None = None
        for lifecycle, _identity in sorted(
            seasons, key=lambda record: record[1].season_number or 0
        ):
            direct = direct_watches.get(lifecycle.id)
            if direct and (latest_prior is None or as_utc(direct) > as_utc(latest_prior)):
                latest_prior = direct
            imported_at = lifecycle.first_imported_at
            if (
                lifecycle.state == "ACTIVE"
                and latest_prior
                and imported_at
                and as_utc(latest_prior) >= as_utc(imported_at)
            ):
                effective_watches[lifecycle.id] = latest_prior
            elif direct is None:
                effective_watches[lifecycle.id] = None
    for lifecycle, _identity_item in records:
        latest = effective_watches.get(lifecycle.id)
        lifecycle.last_meaningful_watch_at = latest
        lifecycle.watched = latest is not None
        lifecycle.retention_deadline = retention_deadline(
            "",
            lifecycle.first_imported_at,
            latest,
            never_watched_weeks=policy.never_watched_weeks,
            watched_weeks=policy.watched_weeks,
        )


def sync_overseerr(
    session: Session,
    integration: IntegrationInstance,
    credentials: dict[str, str],
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    rows = (
        rows
        if rows is not None
        else OverseerrAdapter(integration.base_url, credentials["api_key"]).requests()
    )
    session.query(RequestRecord).filter(RequestRecord.integration_id == integration.id).update(
        {"present": False}
    )
    records_by_external_id = {
        record.external_request_id: record
        for record in session.scalars(
            select(RequestRecord).where(RequestRecord.integration_id == integration.id)
        ).all()
    }
    profiles_by_external_id = {
        profile.external_id: profile
        for profile in session.scalars(
            select(RequesterProfile).where(RequesterProfile.integration_id == integration.id)
        ).all()
    }
    now = utc_now()
    for row in rows:
        request_id = int(row["id"])
        record = records_by_external_id.get(request_id)
        if record is None:
            record = RequestRecord(integration_id=integration.id, external_request_id=request_id)
            session.add(record)
            records_by_external_id[request_id] = record
        media = row.get("media") or {}
        requested_by = row.get("requestedBy") or {}
        record.media_type = str(row.get("type") or media.get("mediaType") or "unknown")
        record.tmdb_id = media.get("tmdbId")
        record.tvdb_id = media.get("tvdbId")
        record.status = str(row.get("status") or media.get("status") or "unknown")
        record.requester_id = str(requested_by.get("id") or "") or None
        record.requested_at = parse_datetime(row.get("createdAt"))
        record.updated_at = parse_datetime(row.get("updatedAt"))
        record.present = True
        if record.requester_id:
            profile = profiles_by_external_id.get(record.requester_id)
            if profile is None:
                profile = RequesterProfile(
                    integration_id=integration.id,
                    external_id=record.requester_id,
                    protected=False,
                )
                session.add(profile)
                profiles_by_external_id[record.requester_id] = profile
            username = requested_by.get("username") or requested_by.get("plexUsername")
            if username:
                profile.username = str(username).strip()
            display_name = requested_by.get("displayName") or username
            if display_name:
                profile.display_name = str(display_name).strip()
            email = requested_by.get("email")
            if email:
                profile.email = str(email).strip()
            profile.last_synced_at = now
    session.flush()
    _apply_request_protection(session)
    return {"requests": len(rows)}


def _provider_id(guids: Any, provider: str) -> int | None:
    if not isinstance(guids, list):
        return None
    for guid in guids:
        raw = guid.get("id") if isinstance(guid, dict) else guid
        value = str(raw or "")
        for marker in (f"{provider}://", f"agents.{provider}://"):
            if marker in value:
                candidate = value.split(marker, 1)[1].split("/", 1)[0].split("?", 1)[0]
                if candidate.isdigit():
                    return int(candidate)
    return None


def fetch_plex_inventory(
    session: Session,
    integration: IntegrationInstance,
    credentials: dict[str, str],
) -> list[tuple[ManagedLibrary, list[dict[str, Any]]]]:
    libraries = session.scalars(
        select(ManagedLibrary).where(
            ManagedLibrary.plex_integration_id == integration.id,
            ManagedLibrary.enabled.is_(True),
        )
    ).all()
    adapter = PlexAdapter(integration.base_url, credentials["api_key"])
    return [
        (library, adapter.library_items(library.external_id, library.media_type))
        for library in libraries
    ]


def sync_plex(
    session: Session,
    integration: IntegrationInstance,
    credentials: dict[str, str],
    policy: InventoryPolicy,
    library_payloads: list[tuple[ManagedLibrary, list[dict[str, Any]]]] | None = None,
) -> dict[str, int]:
    library_payloads = (
        library_payloads
        if library_payloads is not None
        else fetch_plex_inventory(session, integration, credentials)
    )
    item_count = 0
    mapped_count = 0
    for library, items in library_payloads:
        item_count += len(items)
        for item in items:
            rating_key = str(item.get("ratingKey") or "")
            if not rating_key:
                continue
            identity = None
            if library.media_type == "movie":
                tmdb_id = _provider_id(item.get("Guid", []), "tmdb")
                if tmdb_id is not None:
                    identity = session.scalar(
                        select(MediaIdentity).where(MediaIdentity.tmdb_id == tmdb_id)
                    )
            elif library.media_type == "show":
                tvdb_id = _provider_id(item.get("seriesGuids", []), "tvdb")
                season_number = item.get("index")
                if tvdb_id is not None and season_number is not None:
                    identity = session.scalar(
                        select(MediaIdentity).where(
                            MediaIdentity.series_tvdb_id == tvdb_id,
                            MediaIdentity.season_number == int(season_number),
                        )
                    )
            if identity is None:
                continue
            lifecycle = session.scalar(
                select(MediaLifecycle).where(MediaLifecycle.identity_id == identity.id)
            )
            if lifecycle:
                lifecycle.plex_rating_key = rating_key
                lifecycle.library_id = library.id
                mapped_count += 1
    _apply_playback(session, policy)
    return {
        "libraries": len(library_payloads),
        "plex_items": item_count,
        "mapped_lifecycles": mapped_count,
    }


def _apply_request_protection(session: Session) -> None:
    protected_requester = (
        (RequesterProfile.integration_id == RequestRecord.integration_id)
        & (RequesterProfile.external_id == RequestRecord.requester_id)
    )
    active_tmdb = set(
        session.scalars(
            select(RequestRecord.tmdb_id)
            .join(RequesterProfile, protected_requester)
            .where(
                RequestRecord.present.is_(True),
                RequestRecord.tmdb_id.is_not(None),
                RequesterProfile.protected.is_(True),
            )
        ).all()
    )
    active_tvdb = set(
        session.scalars(
            select(RequestRecord.tvdb_id)
            .join(RequesterProfile, protected_requester)
            .where(
                RequestRecord.present.is_(True),
                RequestRecord.tvdb_id.is_not(None),
                RequesterProfile.protected.is_(True),
            )
        ).all()
    )
    for identity, lifecycle, integration in session.execute(
        select(MediaIdentity, MediaLifecycle, IntegrationInstance)
        .join(MediaLifecycle, MediaLifecycle.identity_id == MediaIdentity.id)
        .join(
            IntegrationInstance,
            IntegrationInstance.id == MediaLifecycle.integration_id,
        )
    ):
        requested = (identity.tmdb_id in active_tmdb) or (identity.tvdb_id in active_tvdb)
        base_state, sources = _protection(integration)
        preserved = [
            source
            for source in lifecycle.protection_sources
            if source
            not in {
                "INSTANCE_MODE",
                "INSTANCE_IGNORED",
                "ACTIVE_REQUEST",
                "PROTECTED_REQUESTER",
            }
        ]
        sources.extend(source for source in preserved if source not in sources)
        lifecycle.protection_state = base_state
        if sources:
            lifecycle.protection_state = "PROTECTED"
        if requested:
            sources.append("PROTECTED_REQUESTER")
            lifecycle.protection_state = "PROTECTED"
        lifecycle.protection_sources = sources


def set_requester_protection(
    session: Session,
    profile: RequesterProfile,
    *,
    protected: bool,
) -> None:
    profile.protected = protected
    session.flush()
    _apply_request_protection(session)
    recompute_decisions(session)


def set_manual_protection(
    session: Session,
    lifecycles: list[MediaLifecycle],
    *,
    protected: bool,
) -> list[MediaLifecycle]:
    changed: list[MediaLifecycle] = []
    for lifecycle in lifecycles:
        sources = list(dict.fromkeys(lifecycle.protection_sources or []))
        was_protected = "MANUAL_SELECTION" in sources
        if protected and not was_protected:
            sources.append("MANUAL_SELECTION")
        elif not protected and was_protected:
            sources.remove("MANUAL_SELECTION")
        else:
            continue
        lifecycle.protection_sources = sources
        lifecycle.protection_state = "PROTECTED" if sources else "UNPROTECTED"
        changed.append(lifecycle)
    recompute_decisions(session)
    return changed


def sync_qbittorrent(
    session: Session,
    integration: IntegrationInstance,
    credentials: dict[str, str],
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    rows = (
        rows
        if rows is not None
        else QBittorrentAdapter(integration.base_url, credentials).inventory()
    )
    now = utc_now()
    session.query(Torrent).filter(Torrent.integration_id == integration.id).update(
        {"present": False}
    )
    tracker_count = 0
    for row in rows:
        info_hash = str(row.get("hash") or "").lower()
        if not info_hash:
            continue
        torrent = session.scalar(
            select(Torrent).where(
                Torrent.integration_id == integration.id, Torrent.info_hash == info_hash
            )
        )
        if torrent is None:
            torrent = Torrent(
                integration_id=integration.id,
                info_hash=info_hash,
                name=str(row.get("name") or info_hash),
            )
            session.add(torrent)
            session.flush()
        torrent.name = str(row.get("name") or info_hash)
        torrent.content_path = row.get("content_path")
        torrent.save_path = row.get("save_path")
        torrent.category = row.get("category")
        torrent.tags = [tag.strip() for tag in str(row.get("tags") or "").split(",") if tag.strip()]
        torrent.state = row.get("state")
        torrent.size = row.get("size")
        torrent.amount_left = row.get("amount_left")
        torrent.ratio = row.get("ratio")
        torrent.added_at = parse_datetime(row.get("added_on"))
        torrent.completed_at = parse_datetime(row.get("completion_on"))
        torrent.last_activity_at = parse_datetime(row.get("last_activity"))
        torrent.present = True
        torrent.last_seen_at = now
        session.query(TorrentTracker).filter(TorrentTracker.torrent_id == torrent.id).delete()
        for tracker in row.get("trackers", []):
            url = str(tracker.get("url") or "")
            if not url or url.startswith("**"):
                continue
            session.add(
                TorrentTracker(
                    torrent_id=torrent.id,
                    url=url,
                    host=urlparse(url).hostname or "unknown",
                    tier=tracker.get("tier"),
                    status=tracker.get("status"),
                    message=tracker.get("msg"),
                )
            )
            tracker_count += 1
    _map_torrents(session)
    return {"torrents": len(rows), "trackers": tracker_count}


def _map_torrents(session: Session) -> None:
    current_torrent_ids = select(Torrent.id).where(Torrent.present.is_(True))
    session.query(TorrentMediaMapping).filter(
        TorrentMediaMapping.torrent_id.in_(current_torrent_ids)
    ).delete(synchronize_session=False)
    torrents = session.scalars(select(Torrent).where(Torrent.present.is_(True))).all()
    lifecycles = session.scalars(
        select(MediaLifecycle).where(MediaLifecycle.state == "ACTIVE")
    ).all()
    for torrent in torrents:
        for lifecycle in lifecycles:
            source = None
            confidence = None
            if torrent.info_hash in lifecycle.source_download_ids:
                source, confidence = "ARR_DOWNLOAD_ID", "EXACT"
            elif lifecycle.current_path and torrent.content_path:
                left = lifecycle.current_path.replace("\\", "/").rstrip("/").lower()
                right = torrent.content_path.replace("\\", "/").rstrip("/").lower()
                if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
                    source, confidence = "CONTENT_PATH", "HIGH"
            if source:
                session.add(
                    TorrentMediaMapping(
                        torrent_id=torrent.id,
                        lifecycle_id=lifecycle.id,
                        mapping_source=source,
                        confidence=confidence,
                    )
                )


def sync_integration(
    session: Session, integration: IntegrationInstance, cipher: CredentialCipher
) -> SyncRun:
    if not integration.enabled:
        raise ValueError("Enable the integration before syncing")

    with session.no_autoflush:
        existing_freshness = session.scalar(
            select(SourceFreshness).where(
                SourceFreshness.integration_id == integration.id,
                SourceFreshness.source_kind == integration.kind,
            )
        )
        credentials = cipher.decrypt(integration.credentials_encrypted)
        try:
            if integration.kind in {"RADARR", "SONARR"}:
                fetched: Any = ArrAdapter(integration.base_url, credentials["api_key"]).inventory(
                    integration.kind
                )
            elif integration.kind == "PLEX":
                fetched = fetch_plex_inventory(session, integration, credentials)
            elif integration.kind == "TAUTULLI":
                try:
                    start_offset = int(existing_freshness.cursor if existing_freshness else "0")
                except ValueError:
                    start_offset = 0
                fetched = fetch_tautulli_history(
                    integration,
                    credentials,
                    start_offset=start_offset,
                )
            elif integration.kind == "OVERSEERR":
                fetched = OverseerrAdapter(integration.base_url, credentials["api_key"]).requests()
            elif integration.kind == "QBITTORRENT":
                fetched = QBittorrentAdapter(integration.base_url, credentials).inventory()
            else:
                raise ValueError("This integration does not provide read-only inventory")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            policy = get_inventory_policy(session)
            freshness = _freshness(session, integration, policy)
            message = str(redact(str(error)))[:1000] or "Read-only sync failed"
            run = SyncRun(
                integration_id=integration.id,
                mode="FULL",
                status="FAILED",
                completed_at=utc_now(),
                sanitized_error=message,
            )
            session.add(run)
            freshness.last_attempt_at = utc_now()
            freshness.status = "ERROR"
            freshness.sanitized_error = message
            recompute_decisions(session)
            return run

    policy = get_inventory_policy(session)
    freshness = _freshness(session, integration, policy)
    run = SyncRun(integration_id=integration.id, mode="FULL", status="RUNNING")
    session.add(run)
    freshness.last_attempt_at = utc_now()
    freshness.status = "SYNCING"
    session.flush()
    inventory_savepoint = session.begin_nested()
    try:
        if integration.kind in {"RADARR", "SONARR"}:
            counts = sync_arr(session, integration, credentials, policy, payload=fetched)
        elif integration.kind == "PLEX":
            counts = sync_plex(
                session,
                integration,
                credentials,
                policy,
                library_payloads=fetched,
            )
        elif integration.kind == "TAUTULLI":
            counts = sync_tautulli(
                session,
                integration,
                credentials,
                policy,
                start_offset=start_offset,
                fetched_history=fetched,
            )
        elif integration.kind == "OVERSEERR":
            counts = sync_overseerr(session, integration, credentials, rows=fetched)
        elif integration.kind == "QBITTORRENT":
            counts = sync_qbittorrent(session, integration, credentials, rows=fetched)
        else:
            raise ValueError("This integration does not provide read-only inventory")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        inventory_savepoint.rollback()
        message = str(redact(str(error)))[:1000] or "Read-only sync failed"
        run.status = "FAILED"
        run.completed_at = utc_now()
        run.sanitized_error = message
        freshness.status = "ERROR"
        freshness.sanitized_error = message
        recompute_decisions(session)
        return run
    inventory_savepoint.commit()
    now = utc_now()
    run.status = "SUCCEEDED"
    run.completed_at = now
    run.counts = counts
    freshness.status = "FRESH"
    freshness.last_success_at = now
    freshness.sanitized_error = None
    if integration.kind == "TAUTULLI" and "cursor" in counts:
        freshness.cursor = str(counts.pop("cursor"))
    integration.full_sync_completed_at = now
    recompute_decisions(session)
    return run


def preview_integration(
    session: Session, integration: IntegrationInstance, cipher: CredentialCipher
) -> dict[str, int]:
    """Fetch and count read-only source records without changing persisted inventory."""
    if not integration.enabled:
        raise ValueError("Enable the integration before previewing inventory")
    credentials = cipher.decrypt(integration.credentials_encrypted)
    if integration.kind in {"RADARR", "SONARR"}:
        payload = ArrAdapter(integration.base_url, credentials["api_key"]).inventory(
            integration.kind
        )
        return {
            "items": len(payload.get("items", [])),
            "files": len(payload.get("files", [])),
            "history_records": len(_history_records(payload.get("history"))),
        }
    if integration.kind == "PLEX":
        libraries = session.scalars(
            select(ManagedLibrary).where(
                ManagedLibrary.plex_integration_id == integration.id,
                ManagedLibrary.enabled.is_(True),
            )
        ).all()
        adapter = PlexAdapter(integration.base_url, credentials["api_key"])
        item_count = sum(
            len(adapter.library_items(item.external_id, item.media_type)) for item in libraries
        )
        return {"selected_libraries": len(libraries), "plex_items": item_count}
    if integration.kind == "TAUTULLI":
        payload = TautulliAdapter(integration.base_url, credentials["api_key"]).history(length=1000)
        return {"history_rows": int(payload.get("recordsTotal") or 0)}
    if integration.kind == "OVERSEERR":
        rows = OverseerrAdapter(integration.base_url, credentials["api_key"]).requests()
        return {"requests": len(rows)}
    if integration.kind == "QBITTORRENT":
        rows = QBittorrentAdapter(integration.base_url, credentials).inventory()
        return {
            "torrents": len(rows),
            "trackers": sum(len(item.get("trackers", [])) for item in rows),
        }
    raise ValueError("This integration does not provide read-only inventory")


def source_is_fresh(source: SourceFreshness, now: datetime | None = None) -> bool:
    moment = now or utc_now()
    return bool(
        source.status == "FRESH"
        and source.last_success_at
        and moment - as_utc(source.last_success_at) <= timedelta(seconds=source.stale_after_seconds)
    )


def recompute_decisions(session: Session) -> None:
    policy = get_inventory_policy(session)
    enabled = session.scalars(
        select(IntegrationInstance).where(IntegrationInstance.enabled.is_(True))
    ).all()
    freshness_by_integration = {
        row.integration_id: row for row in session.scalars(select(SourceFreshness)).all()
    }
    required = [
        item
        for item in enabled
        if item.kind in {"RADARR", "SONARR", "PLEX", "TAUTULLI", "OVERSEERR", "QBITTORRENT"}
        and item.management_mode != "IGNORED"
    ]
    stale_sources = tuple(
        item.name
        for item in required
        if item.id not in freshness_by_integration
        or not source_is_fresh(freshness_by_integration[item.id])
    )
    all_fresh = bool(required) and not stale_sources
    for lifecycle in session.scalars(select(MediaLifecycle)).all():
        if (
            lifecycle.first_imported_at
            and lifecycle.last_meaningful_watch_at
            and as_utc(lifecycle.last_meaningful_watch_at) < as_utc(lifecycle.first_imported_at)
        ):
            lifecycle.last_meaningful_watch_at = None
            lifecycle.watched = False
        lifecycle.retention_deadline = retention_deadline(
            "",
            lifecycle.first_imported_at,
            lifecycle.last_meaningful_watch_at,
            never_watched_weeks=policy.never_watched_weeks,
            watched_weeks=policy.watched_weeks,
        )
        _set_decision(
            lifecycle,
            all_required_sources_fresh=all_fresh,
            stale_sources=stale_sources,
        )
