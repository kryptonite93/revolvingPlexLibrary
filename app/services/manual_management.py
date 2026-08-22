from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.arr import ArrAdapter
from app.integrations.plex import PlexAdapter
from app.integrations.qbittorrent import QBittorrentAdapter
from app.persistence.models import (
    IntegrationInstance,
    IntegrationLibraryMapping,
    ManagedLibrary,
    ManualDeletionBatch,
    ManualDeletionItem,
    MediaFileRevision,
    MediaIdentity,
    MediaLifecycle,
    RequesterProfile,
    RequestRecord,
    SourceFreshness,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
    TrackerPolicy,
    utc_now,
)
from app.security.credentials import CredentialCipher
from app.security.redaction import redact
from app.services.dry_run import CONFIDENT_MAPPINGS, _tracker_result, normalize_tracker_domain
from app.services.events import append_event
from app.services.inventory import source_is_fresh
from app.services.rollout import get_rollout_policy

TRACKER_FILTERS = {"ALL", "MET", "NOT_MET"}
WATCH_FILTERS = {"ALL", "WATCHED", "NEVER_WATCHED"}
SORT_FIELDS = {"NAME", "LAST_WATCHED", "REQUEST_DATE", "RELEASE_DATE", "SIZE"}
SORT_DIRECTIONS = {"ASC", "DESC"}
PAGE_SIZE = 25


class ManualManagementError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrackerAssessment:
    state: str
    label: str
    reason: str
    torrent_count: int


@dataclass(frozen=True)
class ManualCandidate:
    lifecycle: MediaLifecycle
    identity: MediaIdentity
    integration: IntegrationInstance
    requested_at: datetime | None
    tracker: TrackerAssessment


@dataclass(frozen=True)
class ManualGroup:
    key: str
    title: str
    media_type: str
    year: int | None
    total_size: int
    meaningfully_watched_count: int
    candidates: list[ManualCandidate]


@dataclass(frozen=True)
class ManualManagementPage:
    requesters: list[RequesterProfile]
    integrations: list[IntegrationInstance]
    selected_requester: RequesterProfile | None
    selected_integration: IntegrationInstance | None
    tracker_filter: str
    watch_filter: str
    sort_field: str
    sort_direction: str
    groups: list[ManualGroup]
    total_groups: int
    total_items: int
    total_size: int
    page: int
    total_pages: int


def _requester_label(profile: RequesterProfile) -> str:
    return profile.display_name or profile.username or profile.email or profile.external_id


def _request_keys(
    session: Session, profile: RequesterProfile
) -> tuple[dict[int, datetime | None], dict[int, datetime | None]]:
    movie_dates: dict[int, datetime | None] = {}
    series_dates: dict[int, datetime | None] = {}
    rows = session.scalars(
        select(RequestRecord).where(
            RequestRecord.integration_id == profile.integration_id,
            RequestRecord.requester_id == profile.external_id,
            RequestRecord.present.is_(True),
        )
    ).all()
    for request in rows:
        media_type = request.media_type.strip().upper()
        if media_type == "MOVIE" and request.tmdb_id is not None:
            current = movie_dates.get(request.tmdb_id)
            if current is None or (request.requested_at and request.requested_at > current):
                movie_dates[request.tmdb_id] = request.requested_at
        if media_type in {"TV", "SERIES", "SHOW"} and request.tvdb_id is not None:
            current = series_dates.get(request.tvdb_id)
            if current is None or (request.requested_at and request.requested_at > current):
                series_dates[request.tvdb_id] = request.requested_at
    return movie_dates, series_dates


def assess_tracker_safety(session: Session, lifecycle_id: str) -> TrackerAssessment:
    mapping_rows = session.execute(
        select(TorrentMediaMapping, Torrent)
        .join(Torrent, Torrent.id == TorrentMediaMapping.torrent_id)
        .where(
            TorrentMediaMapping.lifecycle_id == lifecycle_id,
            Torrent.present.is_(True),
        )
    ).all()
    if not mapping_rows:
        return TrackerAssessment("MET", "No torrent", "No current torrent is associated", 0)

    policies = {
        policy.normalized_domain: policy
        for policy in session.scalars(
            select(TrackerPolicy).where(TrackerPolicy.selected.is_(True))
        ).all()
    }
    for mapping, torrent in mapping_rows:
        if mapping.confidence not in CONFIDENT_MAPPINGS:
            return TrackerAssessment(
                "NOT_MET",
                "Mapping needs review",
                "A torrent mapping is not confident enough for deletion",
                len(mapping_rows),
            )
        mapping_count = int(
            session.scalar(
                select(func.count()).select_from(TorrentMediaMapping).where(
                    TorrentMediaMapping.torrent_id == torrent.id
                )
            )
            or 0
        )
        if mapping_count > 1:
            return TrackerAssessment(
                "NOT_MET",
                "Shared torrent",
                "A mapped torrent is shared with another movie or season",
                len(mapping_rows),
            )
        if torrent.amount_left not in (None, 0):
            return TrackerAssessment(
                "NOT_MET",
                "Download incomplete",
                f"{torrent.name} still has data left to download",
                len(mapping_rows),
            )
        trackers = session.scalars(
            select(TorrentTracker).where(TorrentTracker.torrent_id == torrent.id)
        ).all()
        if not trackers:
            return TrackerAssessment(
                "NOT_MET",
                "Tracker evidence missing",
                f"{torrent.name} has no tracker evidence",
                len(mapping_rows),
            )
        for tracker in trackers:
            domain = normalize_tracker_domain(tracker.host)
            passed, _code, reason, _evidence = _tracker_result(
                torrent,
                tracker,
                policies.get(domain),
                require_automatic_permission=False,
            )
            if not passed:
                return TrackerAssessment(
                    "NOT_MET", "Conditions not met", reason, len(mapping_rows)
                )
    return TrackerAssessment(
        "MET",
        "Conditions met",
        "Every mapped torrent satisfies its tracker rule",
        len(mapping_rows),
    )


def _matching_candidates(
    session: Session,
    profile: RequesterProfile,
    integration: IntegrationInstance,
    tracker_filter: str,
    watch_filter: str,
) -> list[ManualCandidate]:
    movie_dates, series_dates = _request_keys(session, profile)
    if integration.kind == "RADARR" and not movie_dates:
        return []
    if integration.kind == "SONARR" and not series_dates:
        return []

    query = (
        select(MediaLifecycle, MediaIdentity)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .where(
            MediaLifecycle.integration_id == integration.id,
            MediaLifecycle.state == "ACTIVE",
        )
        .order_by(MediaIdentity.canonical_title, MediaIdentity.season_number)
    )
    if integration.kind == "RADARR":
        query = query.where(
            MediaIdentity.media_type == "MOVIE",
            MediaIdentity.tmdb_id.in_(set(movie_dates)),
        )
    else:
        query = query.where(
            MediaIdentity.media_type == "SEASON",
            MediaIdentity.series_tvdb_id.in_(set(series_dates)),
        )
    if watch_filter == "WATCHED":
        query = query.where(MediaLifecycle.last_meaningful_watch_at.is_not(None))
    elif watch_filter == "NEVER_WATCHED":
        query = query.where(MediaLifecycle.last_meaningful_watch_at.is_(None))

    candidates: list[ManualCandidate] = []
    for lifecycle, identity in session.execute(query).all():
        tracker = assess_tracker_safety(session, lifecycle.id)
        if tracker_filter != "ALL" and tracker.state != tracker_filter:
            continue
        requested_at = (
            movie_dates.get(identity.tmdb_id or -1)
            if integration.kind == "RADARR"
            else series_dates.get(identity.series_tvdb_id or -1)
        )
        candidates.append(
            ManualCandidate(lifecycle, identity, integration, requested_at, tracker)
        )
    return candidates


def _groups(candidates: list[ManualCandidate]) -> list[ManualGroup]:
    grouped: dict[str, list[ManualCandidate]] = {}
    for candidate in candidates:
        key = (
            candidate.lifecycle.id
            if candidate.integration.kind == "RADARR"
            else f"series:{candidate.lifecycle.arr_item_id}"
        )
        grouped.setdefault(key, []).append(candidate)
    groups: list[ManualGroup] = []
    for key, items in grouped.items():
        first = items[0]
        if first.integration.kind == "RADARR":
            title = first.identity.canonical_title
            media_type = "MOVIE"
        else:
            title = first.identity.canonical_title.rsplit(" · ", 1)[0]
            media_type = "SERIES"
            items.sort(key=lambda item: item.identity.season_number or 0)
        groups.append(
            ManualGroup(
                key,
                title,
                media_type,
                first.identity.year,
                sum(item.lifecycle.current_size or 0 for item in items),
                sum(
                    item.lifecycle.last_meaningful_watch_at is not None for item in items
                ),
                items,
            )
        )
    return sorted(groups, key=lambda group: group.title.casefold())


def _sort_groups(
    groups: list[ManualGroup], sort_field: str, sort_direction: str
) -> list[ManualGroup]:
    if sort_field == "NAME":
        return sorted(
            groups,
            key=lambda group: group.title.casefold(),
            reverse=sort_direction == "DESC",
        )

    def value(group: ManualGroup) -> float | int | None:
        if sort_field == "SIZE":
            return group.total_size
        if sort_field == "RELEASE_DATE":
            return group.year
        dates = (
            [
                candidate.requested_at
                for candidate in group.candidates
                if candidate.requested_at is not None
            ]
            if sort_field == "REQUEST_DATE"
            else [
                candidate.lifecycle.last_meaningful_watch_at
                for candidate in group.candidates
                if candidate.lifecycle.last_meaningful_watch_at is not None
            ]
        )
        if not dates:
            return None
        latest = max(dates)
        return latest.timestamp()

    known = [group for group in groups if value(group) is not None]
    unknown = [group for group in groups if value(group) is None]
    known.sort(key=lambda group: value(group) or 0, reverse=sort_direction == "DESC")
    unknown.sort(key=lambda group: group.title.casefold())
    return known + unknown


def build_manual_management_page(
    session: Session,
    *,
    requester_profile_id: str = "",
    integration_id: str = "",
    tracker_filter: str = "ALL",
    watch_filter: str = "ALL",
    sort_field: str = "NAME",
    sort_direction: str = "ASC",
    page: int = 1,
) -> ManualManagementPage:
    requesters = session.scalars(
        select(RequesterProfile).order_by(
            RequesterProfile.display_name,
            RequesterProfile.username,
            RequesterProfile.email,
            RequesterProfile.external_id,
        )
    ).all()
    integrations = session.scalars(
        select(IntegrationInstance)
        .where(IntegrationInstance.kind.in_({"RADARR", "SONARR"}))
        .order_by(IntegrationInstance.name)
    ).all()
    selected_requester = session.get(RequesterProfile, requester_profile_id)
    selected_integration = session.get(IntegrationInstance, integration_id)
    normalized_tracker_filter = tracker_filter.upper()
    if normalized_tracker_filter not in TRACKER_FILTERS:
        normalized_tracker_filter = "ALL"
    normalized_watch_filter = watch_filter.upper()
    if normalized_watch_filter not in WATCH_FILTERS:
        normalized_watch_filter = "ALL"
    normalized_sort_field = sort_field.upper()
    if normalized_sort_field not in SORT_FIELDS:
        normalized_sort_field = "NAME"
    normalized_sort_direction = sort_direction.upper()
    if normalized_sort_direction not in SORT_DIRECTIONS:
        normalized_sort_direction = "ASC"
    candidates: list[ManualCandidate] = []
    if (
        selected_requester is not None
        and selected_integration is not None
        and selected_integration.kind in {"RADARR", "SONARR"}
    ):
        candidates = _matching_candidates(
            session,
            selected_requester,
            selected_integration,
            normalized_tracker_filter,
            normalized_watch_filter,
        )
    all_groups = _sort_groups(
        _groups(candidates), normalized_sort_field, normalized_sort_direction
    )
    total_groups = len(all_groups)
    total_pages = max(1, (total_groups + PAGE_SIZE - 1) // PAGE_SIZE)
    current_page = min(max(1, page), total_pages)
    start = (current_page - 1) * PAGE_SIZE
    return ManualManagementPage(
        requesters=requesters,
        integrations=integrations,
        selected_requester=selected_requester,
        selected_integration=selected_integration,
        tracker_filter=normalized_tracker_filter,
        watch_filter=normalized_watch_filter,
        sort_field=normalized_sort_field,
        sort_direction=normalized_sort_direction,
        groups=all_groups[start : start + PAGE_SIZE],
        total_groups=total_groups,
        total_items=len(candidates),
        total_size=sum(candidate.lifecycle.current_size or 0 for candidate in candidates),
        page=current_page,
        total_pages=total_pages,
    )


def resolve_manual_selection(
    session: Session,
    *,
    requester_profile_id: str,
    integration_id: str,
    tracker_filter: str,
    watch_filter: str,
    lifecycle_ids: list[str],
    select_all_filtered: bool,
) -> tuple[RequesterProfile, IntegrationInstance, list[ManualCandidate]]:
    profile = session.get(RequesterProfile, requester_profile_id)
    integration = session.get(IntegrationInstance, integration_id)
    if profile is None:
        raise ManualManagementError("Choose an Overseerr user.")
    if integration is None or integration.kind not in {"RADARR", "SONARR"}:
        raise ManualManagementError("Choose a Radarr or Sonarr instance.")
    normalized_tracker_filter = tracker_filter.upper()
    if normalized_tracker_filter not in TRACKER_FILTERS:
        raise ManualManagementError("Choose a valid tracker condition filter.")
    normalized_watch_filter = watch_filter.upper()
    if normalized_watch_filter not in WATCH_FILTERS:
        raise ManualManagementError("Choose a valid meaningful watch filter.")
    candidates = _matching_candidates(
        session,
        profile,
        integration,
        normalized_tracker_filter,
        normalized_watch_filter,
    )
    by_id = {candidate.lifecycle.id: candidate for candidate in candidates}
    if select_all_filtered:
        selected = candidates
    else:
        requested_ids = set(lifecycle_ids)
        if not requested_ids:
            raise ManualManagementError("Select at least one movie or season.")
        if not requested_ids.issubset(by_id):
            raise ManualManagementError(
                "One or more selected items no longer match the current user and filters."
            )
        selected = [by_id[item_id] for item_id in lifecycle_ids if item_id in by_id]
    if not selected:
        raise ManualManagementError("No downloaded items match the current filters.")
    return profile, integration, selected


def create_manual_batch(
    session: Session,
    *,
    profile: RequesterProfile,
    integration: IntegrationInstance,
    candidates: list[ManualCandidate],
    admin_id: str,
    add_import_exclusion: bool,
) -> ManualDeletionBatch:
    if get_rollout_policy(session).mode != "APPROVAL_REQUIRED":
        raise ManualManagementError("Move the global rollout to Approval Required first.")
    if not integration.enabled or not integration.active_management_enabled:
        raise ManualManagementError(f"Enable Active Management for {integration.name} first.")
    if integration.management_mode != "MANAGED":
        raise ManualManagementError(f"Set {integration.name} to Managed first.")
    batch = ManualDeletionBatch(
        correlation_id=str(uuid.uuid4()),
        requester_profile_id=profile.id,
        integration_id=integration.id,
        requested_by_admin_id=admin_id,
        add_import_exclusion=add_import_exclusion if integration.kind == "RADARR" else False,
        total_items=len(candidates),
    )
    session.add(batch)
    session.flush()
    for candidate in candidates:
        session.add(ManualDeletionItem(batch_id=batch.id, lifecycle_id=candidate.lifecycle.id))
    append_event(
        session,
        event_type="manual_management.batch_prepared",
        entity_type="manual_deletion_batch",
        entity_id=batch.id,
        actor_type="admin",
        actor_id=admin_id,
        correlation_id=batch.correlation_id,
        payload={
            "requester": _requester_label(profile),
            "integration": integration.name,
            "item_count": len(candidates),
            "add_import_exclusion": batch.add_import_exclusion,
            "external_mutations": [],
        },
    )
    session.commit()
    return batch


def _item_record(
    session: Session, item: ManualDeletionItem
) -> tuple[MediaLifecycle, MediaIdentity, IntegrationInstance]:
    record = session.execute(
        select(MediaLifecycle, MediaIdentity, IntegrationInstance)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .join(IntegrationInstance, IntegrationInstance.id == MediaLifecycle.integration_id)
        .where(MediaLifecycle.id == item.lifecycle_id)
    ).one_or_none()
    if record is None:
        raise ManualManagementError("The selected lifecycle is no longer available.")
    return record


def _item_event(
    session: Session,
    batch: ManualDeletionBatch,
    item: ManualDeletionItem,
    event_type: str,
    *,
    admin_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    append_event(
        session,
        event_type=event_type,
        entity_type="manual_deletion_item",
        entity_id=item.id,
        actor_type="admin" if admin_id else "system",
        actor_id=admin_id,
        correlation_id=batch.correlation_id,
        payload=payload or {},
    )


def _transition_item(
    session: Session,
    batch: ManualDeletionBatch,
    item: ManualDeletionItem,
    state: str,
    step: str,
    *,
    payload: dict[str, object] | None = None,
) -> None:
    previous = item.state
    item.state = state
    item.current_step = step
    item.last_error = None
    _item_event(
        session,
        batch,
        item,
        "manual_management.item_transitioned",
        payload={"previous": previous, "current": state, "step": step, **(payload or {})},
    )
    session.commit()


def _require_fresh(session: Session, integration_ids: set[str]) -> None:
    freshness = {
        row.integration_id: row
        for row in session.scalars(
            select(SourceFreshness).where(SourceFreshness.integration_id.in_(integration_ids))
        ).all()
    }
    stale = [
        item_id
        for item_id in integration_ids
        if freshness.get(item_id) is None or not source_is_fresh(freshness[item_id])
    ]
    if stale:
        names = session.scalars(
            select(IntegrationInstance.name).where(IntegrationInstance.id.in_(stale))
        ).all()
        raise ManualManagementError(f"Synchronize first: {', '.join(names)}.")


def _request_still_matches(
    session: Session,
    profile: RequesterProfile,
    identity: MediaIdentity,
) -> bool:
    filters = [
        RequestRecord.integration_id == profile.integration_id,
        RequestRecord.requester_id == profile.external_id,
        RequestRecord.present.is_(True),
    ]
    if identity.media_type == "MOVIE" and identity.tmdb_id is not None:
        filters.append(RequestRecord.tmdb_id == identity.tmdb_id)
    elif identity.media_type == "SEASON" and identity.series_tvdb_id is not None:
        filters.append(RequestRecord.tvdb_id == identity.series_tvdb_id)
    else:
        return False
    return session.scalar(select(RequestRecord.id).where(*filters).limit(1)) is not None


def _live_torrent_snapshots(
    session: Session,
    lifecycle: MediaLifecycle,
    cipher: CredentialCipher,
) -> list[dict[str, object]]:
    rows = session.execute(
        select(TorrentMediaMapping, Torrent, IntegrationInstance)
        .join(Torrent, Torrent.id == TorrentMediaMapping.torrent_id)
        .join(IntegrationInstance, IntegrationInstance.id == Torrent.integration_id)
        .where(
            TorrentMediaMapping.lifecycle_id == lifecycle.id,
            Torrent.present.is_(True),
        )
    ).all()
    snapshots: list[dict[str, object]] = []
    for mapping, torrent, integration in rows:
        if not integration.enabled or not integration.active_management_enabled:
            raise ManualManagementError(f"Enable Active Management for {integration.name} first.")
        adapter = QBittorrentAdapter(
            integration.base_url, cipher.decrypt(integration.credentials_encrypted)
        )
        live = adapter.torrent(torrent.info_hash)
        if live is None:
            torrent.present = False
            continue
        torrent.name = str(live.get("name") or torrent.name)
        torrent.amount_left = live.get("amount_left")
        torrent.ratio = live.get("ratio")
        torrent.seeding_seconds = live.get("seeding_time")
        torrent.last_seen_at = utc_now()
        session.query(TorrentTracker).filter(TorrentTracker.torrent_id == torrent.id).delete()
        for tracker in live.get("trackers", []):
            if not isinstance(tracker, dict):
                continue
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
        snapshots.append(
            {
                "torrent_id": torrent.id,
                "integration_id": integration.id,
                "info_hash": torrent.info_hash,
                "name": torrent.name,
                "mapping_confidence": mapping.confidence,
            }
        )
    session.flush()
    assessment = assess_tracker_safety(session, lifecycle.id)
    if assessment.state != "MET":
        raise ManualManagementError(assessment.reason)
    return snapshots


def _revalidate_item(
    session: Session,
    batch: ManualDeletionBatch,
    item: ManualDeletionItem,
    profile: RequesterProfile,
    cipher: CredentialCipher,
) -> None:
    lifecycle, identity, integration = _item_record(session, item)
    if integration.id != batch.integration_id:
        raise ManualManagementError("The selected item belongs to a different instance.")
    if not integration.enabled or not integration.active_management_enabled:
        raise ManualManagementError(f"Active Management is off for {integration.name}.")
    if integration.management_mode != "MANAGED" or integration.health_status != "HEALTHY":
        raise ManualManagementError(f"{integration.name} is not healthy and Managed.")
    if lifecycle.state != "ACTIVE":
        raise ManualManagementError("This item is no longer downloaded in the selected instance.")
    if not _request_still_matches(session, profile, identity):
        raise ManualManagementError("The item no longer matches this Overseerr user.")
    mapping = session.get(IntegrationLibraryMapping, integration.id)
    if mapping is None:
        raise ManualManagementError(f"Pair {integration.name} with its Plex library first.")
    library = session.get(ManagedLibrary, mapping.library_id)
    if library is None or not library.enabled or lifecycle.library_id != library.id:
        raise ManualManagementError(
            f"Synchronize Plex after pairing {integration.name} with its library."
        )
    plex = session.get(IntegrationInstance, library.plex_integration_id)
    if plex is None or not plex.enabled or plex.health_status != "HEALTHY":
        raise ManualManagementError("The paired Plex integration is unavailable.")
    qbittorrent_ids = set(
        session.scalars(
            select(IntegrationInstance.id).where(
                IntegrationInstance.kind == "QBITTORRENT",
                IntegrationInstance.enabled.is_(True),
            )
        ).all()
    )
    _require_fresh(
        session,
        {profile.integration_id, integration.id, plex.id, *qbittorrent_ids},
    )
    if not lifecycle.plex_rating_key:
        raise ManualManagementError(
            "The selected item does not have an exact Plex identity. "
            "Synchronize Plex and Arr first."
        )
    plex_adapter = PlexAdapter(
        plex.base_url, cipher.decrypt(plex.credentials_encrypted)["api_key"]
    )
    if lifecycle.plex_rating_key in plex_adapter.active_session_rating_keys():
        raise ManualManagementError("This movie or season is currently playing in Plex.")

    arr_adapter = ArrAdapter(
        integration.base_url, cipher.decrypt(integration.credentials_encrypted)["api_key"]
    )
    if integration.kind == "RADARR":
        live = arr_adapter.movie(lifecycle.arr_item_id)
        if live is None or not live.get("hasFile"):
            raise ManualManagementError("Radarr no longer reports a downloaded movie.")
        if int(live.get("tmdbId") or 0) != int(identity.tmdb_id or 0):
            raise ManualManagementError("Radarr returned a different movie identity.")
        file_ids: list[int] = []
    else:
        if identity.season_number is None:
            raise ManualManagementError("The selected item has no verified Sonarr season number.")
        live = arr_adapter.series(lifecycle.arr_item_id)
        if live is None:
            raise ManualManagementError("Sonarr no longer reports this series.")
        if int(live.get("tvdbId") or 0) != int(identity.series_tvdb_id or 0):
            raise ManualManagementError("Sonarr returned a different series identity.")
        season_number = int(identity.season_number)
        file_ids = [
            int(file["id"])
            for file in arr_adapter.episode_files(lifecycle.arr_item_id)
            if file.get("id") is not None and int(file.get("seasonNumber", -1)) == season_number
        ]
        if not file_ids:
            raise ManualManagementError("Sonarr no longer reports files for this season.")

    torrents = _live_torrent_snapshots(session, lifecycle, cipher)
    item.external_state = {
        "arr_kind": integration.kind,
        "arr_item_id": lifecycle.arr_item_id,
        "tmdb_id": identity.tmdb_id,
        "tvdb_id": identity.series_tvdb_id,
        "season_number": identity.season_number,
        "episode_file_ids": file_ids,
        "library_id": library.id,
        "plex_integration_id": plex.id,
        "torrent_snapshots": torrents,
        "torrent_ids_deleted": [],
        "add_import_exclusion": (
            batch.add_import_exclusion if integration.kind == "RADARR" else False
        ),
    }
    _transition_item(session, batch, item, "REVALIDATED", "READY_FOR_ARR_DELETE")


def _execute_arr_delete(
    session: Session,
    batch: ManualDeletionBatch,
    item: ManualDeletionItem,
    cipher: CredentialCipher,
) -> None:
    lifecycle, identity, integration = _item_record(session, item)
    adapter = ArrAdapter(
        integration.base_url, cipher.decrypt(integration.credentials_encrypted)["api_key"]
    )
    if item.state != "ARR_DELETE_REQUESTED":
        _transition_item(session, batch, item, "ARR_DELETE_REQUESTED", "CALLING_ARR_DELETE")
    if integration.kind == "RADARR":
        live = adapter.movie(lifecycle.arr_item_id)
        if live is not None:
            if int(live.get("tmdbId") or 0) != int(item.external_state.get("tmdb_id") or 0):
                raise ManualManagementError("Radarr identity changed before deletion.")
            adapter.delete_movie(
                lifecycle.arr_item_id,
                add_import_exclusion=bool(item.external_state.get("add_import_exclusion")),
            )
        if adapter.movie(lifecycle.arr_item_id) is not None:
            raise ManualManagementError("Radarr still reports the movie after deletion.")
        if item.external_state.get("add_import_exclusion"):
            adapter.ensure_import_exclusion(
                int(item.external_state["tmdb_id"]),
                identity.canonical_title,
                identity.year,
            )
    else:
        stored_season_number = item.external_state.get("season_number")
        if stored_season_number is None:
            raise ManualManagementError("The verified Sonarr season number is unavailable.")
        season_number = int(stored_season_number)
        current_ids = [
            int(file["id"])
            for file in adapter.episode_files(lifecycle.arr_item_id)
            if file.get("id") is not None and int(file.get("seasonNumber", -1)) == season_number
        ]
        adapter.delete_episode_files(current_ids)
        adapter.set_season_monitored(
            lifecycle.arr_item_id, season_number, monitored=False
        )
        remaining = [
            file
            for file in adapter.episode_files(lifecycle.arr_item_id)
            if int(file.get("seasonNumber", -1)) == season_number
        ]
        if remaining:
            raise ManualManagementError("Sonarr still reports episode files for this season.")
    lifecycle.state = "DELETED"
    lifecycle.deleted_at = utc_now()
    lifecycle.monitored = False if integration.kind == "SONARR" else lifecycle.monitored
    lifecycle.decision = "DELETED"
    lifecycle.decision_reason = "Deleted through Manual Management"
    session.query(MediaFileRevision).filter(
        MediaFileRevision.lifecycle_id == lifecycle.id
    ).update({"active": False})
    _transition_item(session, batch, item, "ARR_DELETED", "ARR_DELETE_CONFIRMED")


def _execute_torrent_deletes(
    session: Session,
    batch: ManualDeletionBatch,
    item: ManualDeletionItem,
    cipher: CredentialCipher,
) -> None:
    snapshots = list(item.external_state.get("torrent_snapshots", []))
    if not snapshots:
        _transition_item(session, batch, item, "TORRENT_DELETED", "NO_TORRENT_ASSOCIATED")
        return
    if item.state != "TORRENT_DELETE_REQUESTED":
        _transition_item(
            session, batch, item, "TORRENT_DELETE_REQUESTED", "CALLING_QBITTORRENT_DELETE"
        )
    external_state = dict(item.external_state)
    deleted = set(external_state.get("torrent_ids_deleted", []))
    for snapshot in snapshots:
        torrent_id = str(snapshot["torrent_id"])
        if torrent_id in deleted:
            continue
        integration = session.get(IntegrationInstance, str(snapshot["integration_id"]))
        if (
            integration is None
            or not integration.enabled
            or not integration.active_management_enabled
        ):
            raise ManualManagementError("The mapped qBittorrent integration is unavailable.")
        adapter = QBittorrentAdapter(
            integration.base_url, cipher.decrypt(integration.credentials_encrypted)
        )
        info_hash = str(snapshot["info_hash"])
        if adapter.torrent(info_hash) is not None:
            adapter.delete_torrent(info_hash, delete_files=True)
        if adapter.torrent(info_hash) is not None:
            raise ManualManagementError("qBittorrent still reports a torrent after deletion.")
        deleted.add(torrent_id)
        external_state["torrent_ids_deleted"] = sorted(deleted)
        item.external_state = external_state
        session.commit()
    _transition_item(session, batch, item, "TORRENT_DELETED", "TORRENT_DELETE_CONFIRMED")


def _refresh_plex(
    session: Session,
    batch: ManualDeletionBatch,
    item: ManualDeletionItem,
    cipher: CredentialCipher,
) -> None:
    library = session.get(ManagedLibrary, str(item.external_state.get("library_id")))
    plex = session.get(IntegrationInstance, str(item.external_state.get("plex_integration_id")))
    if library is None or plex is None or not library.enabled or not plex.enabled:
        raise ManualManagementError("The paired Plex library is unavailable for refresh.")
    if item.state != "PLEX_REFRESH_REQUESTED":
        _transition_item(session, batch, item, "PLEX_REFRESH_REQUESTED", "CALLING_PLEX_REFRESH")
    PlexAdapter(
        plex.base_url, cipher.decrypt(plex.credentials_encrypted)["api_key"]
    ).refresh_library(library.external_id)
    item.completed_at = utc_now()
    _transition_item(session, batch, item, "COMPLETED", "PLEX_REFRESH_REQUESTED")


def execute_manual_batch(
    session: Session,
    batch: ManualDeletionBatch,
    cipher: CredentialCipher,
    *,
    admin_id: str,
) -> None:
    profile = session.get(RequesterProfile, batch.requester_profile_id)
    if profile is None:
        raise ManualManagementError("The selected Overseerr user is no longer available.")
    batch.state = "RUNNING"
    session.commit()
    items = session.scalars(
        select(ManualDeletionItem)
        .where(ManualDeletionItem.batch_id == batch.id)
        .order_by(ManualDeletionItem.created_at)
    ).all()
    for item in items:
        if item.state == "COMPLETED":
            continue
        try:
            if item.state in {"PENDING", "BLOCKED"}:
                _revalidate_item(session, batch, item, profile, cipher)
            if item.state in {"REVALIDATED", "ARR_DELETE_REQUESTED"}:
                _execute_arr_delete(session, batch, item, cipher)
            if item.state in {"ARR_DELETED", "TORRENT_DELETE_REQUESTED"}:
                _execute_torrent_deletes(session, batch, item, cipher)
            if item.state in {"TORRENT_DELETED", "PLEX_REFRESH_REQUESTED"}:
                _refresh_plex(session, batch, item, cipher)
        except ManualManagementError as error:
            if item.state in {"PENDING", "REVALIDATED"}:
                item.state = "BLOCKED"
                item.current_step = "SAFETY_GATE_BLOCKED"
            item.last_error = str(redact(str(error)))[:1000]
            _item_event(
                session,
                batch,
                item,
                "manual_management.item_blocked"
                if item.state == "BLOCKED"
                else "manual_management.item_failed",
                payload={"state": item.state, "message": item.last_error},
            )
            session.commit()
        except (httpx.HTTPError, ValueError, KeyError, OSError) as error:
            item.last_error = str(redact(str(error)))[:1000]
            _item_event(
                session,
                batch,
                item,
                "manual_management.item_failed",
                payload={"state": item.state, "message": item.last_error},
            )
            session.commit()

    batch.completed_items = int(
        session.scalar(
            select(func.count()).select_from(ManualDeletionItem).where(
                ManualDeletionItem.batch_id == batch.id,
                ManualDeletionItem.state == "COMPLETED",
            )
        )
        or 0
    )
    batch.failed_items = batch.total_items - batch.completed_items
    batch.state = "COMPLETED" if batch.failed_items == 0 else "ATTENTION_REQUIRED"
    batch.completed_at = utc_now() if batch.state == "COMPLETED" else None
    _item_event(
        session,
        batch,
        items[0],
        "manual_management.batch_finished",
        admin_id=admin_id,
        payload={
            "completed": batch.completed_items,
            "attention_required": batch.failed_items,
        },
    )
    session.commit()


def batch_results(
    session: Session, batch_id: str
) -> tuple[ManualDeletionBatch, list[tuple[ManualDeletionItem, MediaIdentity]]] | None:
    batch = session.get(ManualDeletionBatch, batch_id)
    if batch is None:
        return None
    rows = session.execute(
        select(ManualDeletionItem, MediaIdentity)
        .join(MediaLifecycle, MediaLifecycle.id == ManualDeletionItem.lifecycle_id)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .where(ManualDeletionItem.batch_id == batch.id)
        .order_by(MediaIdentity.canonical_title, MediaIdentity.season_number)
    ).all()
    return batch, list(rows)
