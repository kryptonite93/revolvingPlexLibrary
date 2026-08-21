from __future__ import annotations

import copy
import uuid
from datetime import UTC
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.arr import ArrAdapter
from app.integrations.overseerr import OverseerrAdapter
from app.integrations.plex import PlexAdapter
from app.integrations.qbittorrent import QBittorrentAdapter
from app.persistence.models import (
    DeletionJob,
    DryRunProposal,
    IntegrationInstance,
    ManagedLibrary,
    MediaIdentity,
    MediaLifecycle,
    SourceFreshness,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
    utc_now,
)
from app.security.credentials import CredentialCipher
from app.security.redaction import redact
from app.services.dry_run import CONFIDENT_MAPPINGS, evaluate_dry_run
from app.services.events import append_event
from app.services.inventory import parse_datetime, source_is_fresh
from app.services.rollout import get_rollout_policy

TERMINAL_JOB_STATES = {"COMPLETED", "CANCELLED"}
EXECUTABLE_JOB_STATES = {
    "PENDING_APPROVAL",
    "APPROVED",
    "BLOCKED",
    "FAILED",
    "REVALIDATED",
    "RADARR_DELETE_REQUESTED",
    "RADARR_DELETED",
    "TORRENT_DELETE_REQUESTED",
    "TORRENT_DELETED",
    "PLEX_REFRESH_REQUESTED",
    "PLEX_REFRESHED",
    "RECONCILE_REQUIRED",
}


class DeletionJobError(RuntimeError):
    pass


class DeletionBlocked(DeletionJobError):
    pass


def _job_record(
    session: Session, job: DeletionJob
) -> tuple[MediaLifecycle, MediaIdentity, IntegrationInstance, DryRunProposal | None]:
    record = session.execute(
        select(MediaLifecycle, MediaIdentity, IntegrationInstance)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .join(IntegrationInstance, IntegrationInstance.id == MediaLifecycle.integration_id)
        .where(MediaLifecycle.id == job.lifecycle_id)
    ).one_or_none()
    if record is None:
        raise DeletionJobError("The job's lifecycle evidence is no longer available.")
    lifecycle, identity, integration = record
    proposal = session.scalar(
        select(DryRunProposal).where(DryRunProposal.lifecycle_id == lifecycle.id)
    )
    return lifecycle, identity, integration, proposal


def _event(
    session: Session,
    job: DeletionJob,
    event_type: str,
    *,
    actor_type: str,
    actor_id: str | None,
    payload: dict[str, object] | None = None,
) -> None:
    append_event(
        session,
        event_type=event_type,
        entity_type="deletion_job",
        entity_id=job.id,
        actor_type=actor_type,
        actor_id=actor_id,
        payload=payload,
        correlation_id=job.correlation_id,
    )


def _transition(
    session: Session,
    job: DeletionJob,
    state: str,
    step: str,
    *,
    actor_type: str = "system",
    actor_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    previous = job.state
    job.state = state
    job.current_step = step
    job.last_error = None
    job.failure_code = None
    _event(
        session,
        job,
        "deletion.job_transitioned",
        actor_type=actor_type,
        actor_id=actor_id,
        payload={"previous": previous, "current": state, "step": step, **(payload or {})},
    )
    session.commit()


def _fail(
    session: Session,
    job: DeletionJob,
    *,
    state: str,
    code: str,
    message: str,
) -> None:
    job.state = state
    job.current_step = code
    job.failure_code = code
    job.last_error = str(redact(message))[:1000]
    _event(
        session,
        job,
        "deletion.job_blocked" if state == "BLOCKED" else "deletion.job_failed",
        actor_type="system",
        actor_id=None,
        payload={"state": state, "code": code, "message": job.last_error},
    )
    session.commit()


def create_movie_job(
    session: Session,
    *,
    lifecycle_id: str,
    admin_id: str,
) -> DeletionJob:
    if get_rollout_policy(session).mode != "APPROVAL_REQUIRED":
        raise DeletionJobError("Move the global rollout to Approval Required first.")
    record = session.execute(
        select(MediaLifecycle, MediaIdentity, IntegrationInstance, DryRunProposal)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .join(IntegrationInstance, IntegrationInstance.id == MediaLifecycle.integration_id)
        .join(DryRunProposal, DryRunProposal.lifecycle_id == MediaLifecycle.id)
        .where(MediaLifecycle.id == lifecycle_id)
    ).one_or_none()
    if record is None:
        raise DeletionJobError("No current deletion proposal exists for this title.")
    lifecycle, identity, integration, proposal = record
    if identity.media_type != "MOVIE" or integration.kind != "RADARR":
        raise DeletionJobError("Manual execution is currently limited to Radarr movies.")
    if integration.management_mode != "MANAGED":
        raise DeletionJobError("This Radarr instance is protected from deletion.")
    if not integration.enabled or not integration.active_management_enabled:
        raise DeletionJobError("Enable Active Management for this Radarr instance first.")
    if lifecycle.state != "ACTIVE" or proposal.state != "ELIGIBLE":
        raise DeletionJobError("Only a currently eligible downloaded movie can be prepared.")
    existing = session.scalar(
        select(DeletionJob).where(DeletionJob.lifecycle_id == lifecycle.id)
    )
    if existing is not None:
        return existing
    job = DeletionJob(
        lifecycle_id=lifecycle.id,
        proposal_id=proposal.id,
        correlation_id=str(uuid.uuid4()),
        requested_by_admin_id=admin_id,
        approval_snapshot=copy.deepcopy(proposal.eligibility_snapshot),
        external_state={},
    )
    session.add(job)
    session.flush()
    _event(
        session,
        job,
        "deletion.job_prepared",
        actor_type="admin",
        actor_id=admin_id,
        payload={
            "title": identity.canonical_title,
            "radarr_instance": integration.name,
            "arr_item_id": lifecycle.arr_item_id,
            "external_mutations": [],
        },
    )
    session.commit()
    return job


def approve_movie_job(
    session: Session,
    job: DeletionJob,
    *,
    admin_id: str,
    confirmation_title: str,
) -> None:
    _lifecycle, identity, _integration, proposal = _job_record(session, job)
    if job.state != "PENDING_APPROVAL":
        raise DeletionJobError("This job is not awaiting approval.")
    if proposal is None or proposal.state != "ELIGIBLE":
        raise DeletionJobError("The deletion proposal is no longer eligible.")
    if confirmation_title.strip() != identity.canonical_title:
        raise DeletionJobError(f"Type {identity.canonical_title} exactly to approve this job.")
    _authorize_movie_job(session, job, admin_id=admin_id)


def _authorize_movie_job(session: Session, job: DeletionJob, *, admin_id: str) -> None:
    _lifecycle, identity, _integration, proposal = _job_record(session, job)
    if job.state != "PENDING_APPROVAL":
        raise DeletionJobError("This job is not awaiting approval.")
    if proposal is None or proposal.state != "ELIGIBLE":
        raise DeletionJobError("The deletion proposal is no longer eligible.")
    job.approved_by_admin_id = admin_id
    job.approved_at = utc_now()
    _transition(
        session,
        job,
        "APPROVED",
        "AWAITING_EXECUTION",
        actor_type="admin",
        actor_id=admin_id,
        payload={"title": identity.canonical_title, "external_mutations": []},
    )


def cancel_movie_job(session: Session, job: DeletionJob, *, admin_id: str) -> None:
    if job.state not in {"PENDING_APPROVAL", "APPROVED", "BLOCKED", "FAILED"}:
        raise DeletionJobError("This job can no longer be cancelled safely.")
    _transition(
        session,
        job,
        "CANCELLED",
        "CANCELLED_BY_ADMIN",
        actor_type="admin",
        actor_id=admin_id,
    )


def external_change_started(job: DeletionJob) -> bool:
    return job.state in {
        "RADARR_DELETE_REQUESTED",
        "RADARR_DELETED",
        "TORRENT_DELETE_REQUESTED",
        "TORRENT_DELETED",
        "PLEX_REFRESH_REQUESTED",
        "PLEX_REFRESHED",
        "RECONCILE_REQUIRED",
        "COMPLETED",
    }


def invalidate_queued_jobs(session: Session, *, reason: str) -> int:
    jobs = session.scalars(
        select(DeletionJob).where(DeletionJob.state.in_({"PENDING_APPROVAL", "APPROVED"}))
    ).all()
    for job in jobs:
        _transition(
            session,
            job,
            "CANCELLED",
            reason,
            payload={"reason": reason},
        )
    return len(jobs)


def _enabled_integration(session: Session, kind: str) -> IntegrationInstance:
    rows = session.scalars(
        select(IntegrationInstance).where(
            IntegrationInstance.kind == kind,
            IntegrationInstance.enabled.is_(True),
        )
    ).all()
    if len(rows) != 1:
        raise DeletionBlocked(f"Exactly one enabled {kind.title()} integration is required.")
    integration = rows[0]
    if integration.health_status != "HEALTHY":
        raise DeletionBlocked(f"{integration.name} is not healthy.")
    return integration


def _ensure_capability_gates(
    session: Session,
    job: DeletionJob,
) -> tuple[MediaLifecycle, MediaIdentity, IntegrationInstance, DryRunProposal | None]:
    lifecycle, identity, integration, proposal = _job_record(session, job)
    if get_rollout_policy(session).mode != "APPROVAL_REQUIRED":
        raise DeletionBlocked("Global rollout is not Approval Required.")
    if identity.media_type != "MOVIE" or integration.kind != "RADARR":
        raise DeletionBlocked("Only managed Radarr movies can execute in this rollout.")
    if not integration.enabled:
        raise DeletionBlocked(f"{integration.name} is disabled.")
    if not integration.active_management_enabled:
        raise DeletionBlocked(f"Active Management is off for {integration.name}.")
    if integration.management_mode != "MANAGED":
        raise DeletionBlocked(f"{integration.name} is not Managed.")
    if integration.health_status != "HEALTHY":
        raise DeletionBlocked(f"{integration.name} is not healthy.")
    return lifecycle, identity, integration, proposal


def _require_fresh_sources(session: Session, integration_ids: set[str]) -> None:
    rows = {
        source.integration_id: source
        for source in session.scalars(
            select(SourceFreshness).where(SourceFreshness.integration_id.in_(integration_ids))
        ).all()
    }
    stale = [
        integration_id
        for integration_id in integration_ids
        if rows.get(integration_id) is None
        or not source_is_fresh(rows[integration_id])
    ]
    if stale:
        names = session.scalars(
            select(IntegrationInstance.name).where(IntegrationInstance.id.in_(stale))
        ).all()
        raise DeletionBlocked(f"Fresh synchronization is required for: {', '.join(names)}.")


def _refresh_torrent_evidence(
    session: Session,
    cipher: CredentialCipher,
    mappings: list[tuple[TorrentMediaMapping, Torrent, IntegrationInstance]],
) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    for mapping, torrent, integration in mappings:
        if not integration.enabled or not integration.active_management_enabled:
            raise DeletionBlocked(f"Active Management is off for {integration.name}.")
        credentials = cipher.decrypt(integration.credentials_encrypted)
        live = QBittorrentAdapter(integration.base_url, credentials).torrent(torrent.info_hash)
        if live is None:
            raise DeletionBlocked(f"{torrent.name} is no longer present in qBittorrent.")
        torrent.name = str(live.get("name") or torrent.name)
        torrent.state = live.get("state")
        torrent.amount_left = live.get("amount_left")
        torrent.ratio = live.get("ratio")
        torrent.seeding_seconds = live.get("seeding_time")
        torrent.completed_at = parse_datetime(live.get("completion_on"))
        torrent.last_activity_at = parse_datetime(live.get("last_activity"))
        torrent.present = True
        torrent.last_seen_at = utc_now()
        session.query(TorrentTracker).filter(TorrentTracker.torrent_id == torrent.id).delete()
        tracker_hosts: list[str] = []
        for tracker in live.get("trackers", []):
            url = str(tracker.get("url") or "")
            if not url or url.startswith("**"):
                continue
            host = urlparse(url).hostname or "unknown"
            tracker_hosts.append(host)
            session.add(
                TorrentTracker(
                    torrent_id=torrent.id,
                    url=url,
                    host=host,
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
                "ratio": torrent.ratio,
                "seeding_seconds": torrent.seeding_seconds,
                "amount_left": torrent.amount_left,
                "trackers": tracker_hosts,
            }
        )
    session.flush()
    return snapshots


def _live_revalidate(
    session: Session,
    job: DeletionJob,
    cipher: CredentialCipher,
) -> None:
    lifecycle, identity, integration, _proposal = _ensure_capability_gates(session, job)
    if lifecycle.state != "ACTIVE":
        raise DeletionBlocked("The movie is no longer an active downloaded lifecycle.")

    plex = _enabled_integration(session, "PLEX")
    tautulli = _enabled_integration(session, "TAUTULLI")
    overseerr = _enabled_integration(session, "OVERSEERR")
    library = session.get(ManagedLibrary, lifecycle.library_id) if lifecycle.library_id else None
    if library is None or not library.enabled or library.plex_integration_id != plex.id:
        raise DeletionBlocked("The movie is not mapped to an enabled Plex library.")

    mapping_rows = session.execute(
        select(TorrentMediaMapping, Torrent, IntegrationInstance)
        .join(Torrent, Torrent.id == TorrentMediaMapping.torrent_id)
        .join(IntegrationInstance, IntegrationInstance.id == Torrent.integration_id)
        .where(
            TorrentMediaMapping.lifecycle_id == lifecycle.id,
            Torrent.present.is_(True),
        )
    ).all()
    for mapping, torrent, _qbit in mapping_rows:
        if mapping.confidence not in CONFIDENT_MAPPINGS:
            raise DeletionBlocked("A torrent mapping is not confident enough to execute.")
        mapping_count = session.scalar(
            select(func.count()).select_from(TorrentMediaMapping).where(
                TorrentMediaMapping.torrent_id == torrent.id
            )
        )
        if int(mapping_count or 0) > 1:
            raise DeletionBlocked("A mapped torrent is shared with another media lifecycle.")

    integration_ids = {integration.id, plex.id, tautulli.id, overseerr.id}
    integration_ids.update(qbit.id for _mapping, _torrent, qbit in mapping_rows)
    _require_fresh_sources(session, integration_ids)

    arr_credentials = cipher.decrypt(integration.credentials_encrypted)
    arr_adapter = ArrAdapter(integration.base_url, arr_credentials["api_key"])
    live_movie = arr_adapter.movie(lifecycle.arr_item_id)
    if live_movie is None or not live_movie.get("hasFile"):
        raise DeletionBlocked("Radarr no longer reports this movie with a downloaded file.")
    live_tmdb_id = int(live_movie.get("tmdbId") or 0)
    if live_tmdb_id <= 0:
        raise DeletionBlocked(
            "Radarr did not provide a TMDb ID, so an import exclusion cannot be guaranteed."
        )
    if identity.tmdb_id and live_tmdb_id != identity.tmdb_id:
        raise DeletionBlocked("Radarr returned a different TMDb identity for this movie.")
    identity.tmdb_id = live_tmdb_id

    plex_credentials = cipher.decrypt(plex.credentials_encrypted)
    plex_adapter = PlexAdapter(plex.base_url, plex_credentials["api_key"])
    active_keys = plex_adapter.active_session_rating_keys()
    if lifecycle.plex_rating_key and lifecycle.plex_rating_key in active_keys:
        raise DeletionBlocked("This movie is currently playing in Plex.")

    torrent_snapshots = _refresh_torrent_evidence(session, cipher, list(mapping_rows))
    evaluate_dry_run(session)
    refreshed_proposal = session.scalar(
        select(DryRunProposal).where(DryRunProposal.lifecycle_id == lifecycle.id)
    )
    if refreshed_proposal is None or refreshed_proposal.state != "ELIGIBLE":
        reason = refreshed_proposal.reason_text if refreshed_proposal else "proposal unavailable"
        raise DeletionBlocked(f"Fresh evidence is no longer eligible: {reason}")

    overseerr_credentials = cipher.decrypt(overseerr.credentials_encrypted)
    overseerr_snapshot = (
        OverseerrAdapter(overseerr.base_url, overseerr_credentials["api_key"]).movie(
            identity.tmdb_id
        )
        if identity.tmdb_id
        else {}
    )
    job.execution_snapshot = {
        "evaluated_at": utc_now().astimezone(UTC).isoformat(),
        "title": identity.canonical_title,
        "tmdb_id": identity.tmdb_id,
        "arr": {
            "integration_id": integration.id,
            "arr_item_id": lifecycle.arr_item_id,
            "has_file": bool(live_movie.get("hasFile")),
            "add_import_exclusion": True,
        },
        "plex": {
            "integration_id": plex.id,
            "library_id": library.id,
            "section_id": library.external_id,
            "rating_key": lifecycle.plex_rating_key,
            "active_session": False,
        },
        "overseerr": {
            "integration_id": overseerr.id,
            "media_status": (overseerr_snapshot.get("mediaInfo") or {}).get("status"),
        },
        "torrents": torrent_snapshots,
    }
    job.external_state = {
        "torrent_ids_deleted": [],
        "plex_refresh_sent": False,
        "plex_item_present": bool(lifecycle.plex_rating_key),
        "overseerr_requestable": False,
    }
    job.attempt_count += 1
    job.started_at = job.started_at or utc_now()
    _transition(session, job, "REVALIDATED", "READY_FOR_RADARR_DELETE")


def _integration_adapter(
    session: Session,
    cipher: CredentialCipher,
    integration_id: str,
) -> QBittorrentAdapter:
    integration = session.get(IntegrationInstance, integration_id)
    if integration is None or integration.kind != "QBITTORRENT":
        raise DeletionJobError("The mapped qBittorrent integration is unavailable.")
    if not integration.enabled or not integration.active_management_enabled:
        raise DeletionBlocked(f"Active Management is off for {integration.name}.")
    return QBittorrentAdapter(
        integration.base_url,
        cipher.decrypt(integration.credentials_encrypted),
    )


def _execute_radarr_delete(
    session: Session,
    job: DeletionJob,
    cipher: CredentialCipher,
) -> None:
    lifecycle, _identity, integration, _proposal = _ensure_capability_gates(session, job)
    verified_tmdb_id = _verified_snapshot_tmdb_id(session, job)
    credentials = cipher.decrypt(integration.credentials_encrypted)
    adapter = ArrAdapter(integration.base_url, credentials["api_key"])
    if job.state != "RADARR_DELETE_REQUESTED":
        _transition(session, job, "RADARR_DELETE_REQUESTED", "CALLING_RADARR_DELETE")
    live_movie = adapter.movie(lifecycle.arr_item_id)
    if live_movie is not None:
        live_tmdb_id = int(live_movie.get("tmdbId") or 0)
        if live_tmdb_id != verified_tmdb_id:
            raise DeletionBlocked(
                "Radarr now reports a different or missing TMDb identity for this item; "
                "the delete was stopped."
            )
        adapter.delete_movie(lifecycle.arr_item_id, add_import_exclusion=True)
    if adapter.movie(lifecycle.arr_item_id) is not None:
        raise DeletionJobError("Radarr accepted the request but still reports the movie.")
    _ensure_radarr_import_exclusion(session, job, cipher)
    lifecycle.state = "DELETED"
    lifecycle.deleted_at = utc_now()
    lifecycle.decision = "DELETED"
    lifecycle.decision_reason = (
        "Deleted through an explicitly approved Radarr workflow with import exclusion"
    )
    _transition(
        session,
        job,
        "RADARR_DELETED",
        "RADARR_DELETE_AND_EXCLUSION_CONFIRMED",
    )


def _verified_snapshot_tmdb_id(session: Session, job: DeletionJob) -> int:
    _lifecycle, identity, _integration, _proposal = _job_record(session, job)
    try:
        tmdb_id = int(job.execution_snapshot.get("tmdb_id") or 0)
    except (TypeError, ValueError):
        tmdb_id = 0
    if tmdb_id <= 0:
        raise DeletionBlocked(
            "This job has no live-verified TMDb ID, so its Radarr import exclusion "
            "cannot be guaranteed. Prepare a new deletion case after synchronizing Radarr."
        )
    if identity.tmdb_id and identity.tmdb_id != tmdb_id:
        raise DeletionBlocked("The stored TMDb identity no longer matches this deletion job.")
    return tmdb_id


def _ensure_radarr_import_exclusion(
    session: Session,
    job: DeletionJob,
    cipher: CredentialCipher,
) -> None:
    lifecycle, identity, integration, _proposal = _ensure_capability_gates(session, job)
    tmdb_id = _verified_snapshot_tmdb_id(session, job)
    adapter = ArrAdapter(
        integration.base_url,
        cipher.decrypt(integration.credentials_encrypted)["api_key"],
    )
    created = adapter.ensure_import_exclusion(
        tmdb_id,
        identity.canonical_title,
        identity.year,
    )
    external_state = dict(job.external_state)
    external_state["radarr_import_exclusion_confirmed"] = True
    job.external_state = external_state
    if created:
        _event(
            session,
            job,
            "deletion.radarr_import_exclusion_created",
            actor_type="system",
            actor_id=None,
            payload={
                "arr_item_id": lifecycle.arr_item_id,
                "tmdb_id": tmdb_id,
            },
        )
    session.commit()


def _execute_torrent_deletes(
    session: Session,
    job: DeletionJob,
    cipher: CredentialCipher,
) -> None:
    _ensure_capability_gates(session, job)
    _ensure_radarr_import_exclusion(session, job, cipher)
    torrents = list(job.execution_snapshot.get("torrents", []))
    external_state = dict(job.external_state)
    deleted = set(external_state.get("torrent_ids_deleted", []))
    if not torrents:
        _transition(
            session,
            job,
            "TORRENT_DELETED",
            "NO_CURRENT_TORRENT_ASSOCIATED",
        )
        return
    if job.state != "TORRENT_DELETE_REQUESTED":
        _transition(
            session,
            job,
            "TORRENT_DELETE_REQUESTED",
            "CALLING_QBITTORRENT_DELETE",
        )
    for snapshot in torrents:
        torrent_id = str(snapshot["torrent_id"])
        if torrent_id in deleted:
            continue
        adapter = _integration_adapter(
            session, cipher, str(snapshot["integration_id"])
        )
        info_hash = str(snapshot["info_hash"])
        if adapter.torrent(info_hash) is not None:
            adapter.delete_torrent(info_hash, delete_files=True)
        if adapter.torrent(info_hash) is not None:
            raise DeletionJobError("qBittorrent still reports a torrent after deletion.")
        deleted.add(torrent_id)
        external_state["torrent_ids_deleted"] = sorted(deleted)
        job.external_state = external_state
        session.commit()
    _transition(session, job, "TORRENT_DELETED", "TORRENT_DELETE_CONFIRMED")


def _refresh_and_reconcile(
    session: Session,
    job: DeletionJob,
    cipher: CredentialCipher,
) -> None:
    _ensure_capability_gates(session, job)
    _ensure_radarr_import_exclusion(session, job, cipher)
    lifecycle, identity, _integration, _proposal = _job_record(session, job)
    plex_snapshot = dict(job.execution_snapshot.get("plex", {}))
    plex = session.get(IntegrationInstance, str(plex_snapshot.get("integration_id")))
    if plex is None or not plex.enabled:
        raise DeletionBlocked("Plex is disabled or unavailable.")
    plex_adapter = PlexAdapter(
        plex.base_url,
        cipher.decrypt(plex.credentials_encrypted)["api_key"],
    )
    if job.state not in {"PLEX_REFRESH_REQUESTED", "PLEX_REFRESHED", "RECONCILE_REQUIRED"}:
        _transition(session, job, "PLEX_REFRESH_REQUESTED", "CALLING_PLEX_REFRESH")
    plex_adapter.refresh_library(str(plex_snapshot["section_id"]))
    external_state = dict(job.external_state)
    external_state["plex_refresh_sent"] = True
    rating_key = lifecycle.plex_rating_key
    external_state["plex_item_present"] = bool(
        rating_key and plex_adapter.item_present(rating_key)
    )
    job.external_state = external_state
    _transition(session, job, "PLEX_REFRESHED", "VERIFYING_REQUESTABILITY")

    overseerr = _enabled_integration(session, "OVERSEERR")
    requestable = False
    media_status: int | None = None
    if identity.tmdb_id:
        payload = OverseerrAdapter(
            overseerr.base_url,
            cipher.decrypt(overseerr.credentials_encrypted)["api_key"],
        ).movie(identity.tmdb_id)
        media_info = payload.get("mediaInfo")
        if isinstance(media_info, dict) and media_info.get("status") is not None:
            media_status = int(media_info["status"])
        requestable = media_info is None or media_status in {None, 1}
    external_state = dict(job.external_state)
    external_state["overseerr_requestable"] = requestable
    external_state["overseerr_media_status"] = media_status
    job.external_state = external_state
    if external_state.get("plex_item_present") or not requestable:
        job.completed_at = None
        _transition(
            session,
            job,
            "RECONCILE_REQUIRED",
            "WAITING_FOR_PLEX_AND_OVERSEERR",
            payload={
                "plex_item_present": bool(external_state.get("plex_item_present")),
                "overseerr_requestable": requestable,
                "overseerr_media_status": media_status,
            },
        )
        return
    job.completed_at = utc_now()
    _transition(session, job, "COMPLETED", "REQUESTABILITY_CONFIRMED")


def execute_movie_job(
    session: Session,
    job: DeletionJob,
    cipher: CredentialCipher,
    *,
    admin_id: str,
) -> None:
    if job.state not in EXECUTABLE_JOB_STATES:
        raise DeletionJobError("This job is not approved for execution.")
    if job.state == "PENDING_APPROVAL":
        _authorize_movie_job(session, job, admin_id=admin_id)
    _lifecycle, identity, _integration, _proposal = _job_record(session, job)
    _event(
        session,
        job,
        "deletion.execution_confirmed",
        actor_type="admin",
        actor_id=admin_id,
        payload={"title": identity.canonical_title},
    )
    session.commit()
    try:
        if job.state in {"APPROVED", "BLOCKED", "FAILED"}:
            _live_revalidate(session, job, cipher)
        if job.state in {"REVALIDATED", "RADARR_DELETE_REQUESTED"}:
            _execute_radarr_delete(session, job, cipher)
        if job.state in {"RADARR_DELETED", "TORRENT_DELETE_REQUESTED"}:
            _execute_torrent_deletes(session, job, cipher)
        if job.state in {
            "TORRENT_DELETED",
            "PLEX_REFRESH_REQUESTED",
            "PLEX_REFRESHED",
            "RECONCILE_REQUIRED",
        }:
            _refresh_and_reconcile(session, job, cipher)
    except DeletionBlocked as error:
        failure_state = job.state if external_change_started(job) else "BLOCKED"
        _fail(
            session,
            job,
            state=failure_state,
            code="SAFETY_GATE_BLOCKED",
            message=str(error),
        )
        raise
    except (httpx.HTTPError, ValueError, KeyError, OSError, DeletionJobError) as error:
        failed_state = job.state if job.state in {
            "RADARR_DELETE_REQUESTED",
            "RADARR_DELETED",
            "TORRENT_DELETE_REQUESTED",
        } else "RECONCILE_REQUIRED" if job.state in {
            "TORRENT_DELETED",
            "PLEX_REFRESH_REQUESTED",
            "PLEX_REFRESHED",
            "RECONCILE_REQUIRED",
        } else "FAILED"
        _fail(
            session,
            job,
            state=failed_state,
            code="EXTERNAL_STEP_FAILED",
            message=str(error),
        )
        raise DeletionJobError(str(error)) from error


def retry_movie_reconciliation(
    session: Session,
    job: DeletionJob,
    cipher: CredentialCipher,
    *,
    admin_id: str,
) -> None:
    if job.state != "RECONCILE_REQUIRED":
        raise DeletionJobError("This job is not waiting for reconciliation.")
    _event(
        session,
        job,
        "deletion.reconciliation_retried",
        actor_type="admin",
        actor_id=admin_id,
    )
    session.commit()
    try:
        _refresh_and_reconcile(session, job, cipher)
    except (httpx.HTTPError, ValueError, KeyError, OSError, DeletionJobError) as error:
        _fail(
            session,
            job,
            state="RECONCILE_REQUIRED",
            code="RECONCILIATION_FAILED",
            message=str(error),
        )
        raise DeletionJobError(str(error)) from error
