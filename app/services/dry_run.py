from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    DryRunProposal,
    IntegrationInstance,
    MediaIdentity,
    MediaLifecycle,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
    TrackerPolicy,
    utc_now,
)

POLICY_COMBINATIONS = {
    "RATIO_ONLY",
    "TIME_ONLY",
    "RATIO_OR_TIME",
    "RATIO_AND_TIME",
    "NEVER_REMOVE",
}
CONFIDENT_MAPPINGS = {"EXACT", "HIGH"}


@dataclass(frozen=True)
class DryRunSummary:
    evaluated: int
    eligible: int
    blocked: int
    retained: int
    protected: int
    estimated_bytes: int
    evaluated_at: datetime


def normalize_tracker_domain(value: str) -> str:
    candidate = value.strip().casefold()
    if "://" in candidate:
        candidate = urlparse(candidate).hostname or ""
    return candidate.rstrip(".")


def discovered_tracker_domains(session: Session) -> list[tuple[str, int]]:
    totals: dict[str, int] = {}
    for host, count in session.execute(
        select(TorrentTracker.host, func.count(func.distinct(TorrentTracker.torrent_id)))
        .join(Torrent, Torrent.id == TorrentTracker.torrent_id)
        .where(Torrent.present.is_(True))
        .group_by(TorrentTracker.host)
        .order_by(TorrentTracker.host)
    ):
        domain = normalize_tracker_domain(host)
        if domain:
            totals[domain] = totals.get(domain, 0) + int(count)
    return sorted(totals.items())


def save_tracker_policy(
    session: Session,
    *,
    domain: str,
    minimum_ratio: float | None,
    minimum_seed_seconds: int | None,
    combination: str,
    grace_period_seconds: int,
    automatic_deletion_allowed: bool,
) -> TrackerPolicy:
    normalized = normalize_tracker_domain(domain)
    if not normalized or len(normalized) > 240 or "." not in normalized:
        raise ValueError("Choose a valid discovered tracker domain.")
    if combination not in POLICY_COMBINATIONS:
        raise ValueError("Choose a valid tracker rule.")
    if combination in {"RATIO_ONLY", "RATIO_OR_TIME", "RATIO_AND_TIME"} and (
        minimum_ratio is None or minimum_ratio <= 0 or minimum_ratio > 1000
    ):
        raise ValueError("Minimum ratio must be greater than 0 and no more than 1000.")
    if combination in {"TIME_ONLY", "RATIO_OR_TIME", "RATIO_AND_TIME"} and (
        minimum_seed_seconds is None
        or minimum_seed_seconds <= 0
        or minimum_seed_seconds > 315_360_000
    ):
        raise ValueError("Minimum seed time must be greater than 0 and no more than 10 years.")
    if grace_period_seconds < 0 or grace_period_seconds > 31_536_000:
        raise ValueError("Grace period must be between 0 and 365 days.")

    policy = session.scalar(
        select(TrackerPolicy).where(TrackerPolicy.normalized_domain == normalized)
    )
    if policy is None:
        policy = TrackerPolicy(normalized_domain=normalized, combination=combination)
        session.add(policy)
    policy.selected = True
    policy.minimum_ratio = (
        minimum_ratio
        if combination in {"RATIO_ONLY", "RATIO_OR_TIME", "RATIO_AND_TIME"}
        else None
    )
    policy.minimum_seed_seconds = (
        minimum_seed_seconds
        if combination in {"TIME_ONLY", "RATIO_OR_TIME", "RATIO_AND_TIME"}
        else None
    )
    policy.combination = combination
    policy.grace_period_seconds = grace_period_seconds
    policy.automatic_deletion_allowed = automatic_deletion_allowed
    session.flush()
    return policy


def _hours(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 86_400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86_400:.1f}d"


def _tracker_result(
    torrent: Torrent, tracker: TorrentTracker, policy: TrackerPolicy | None
) -> tuple[bool, str, str, dict[str, object]]:
    domain = normalize_tracker_domain(tracker.host)
    evidence: dict[str, object] = {
        "domain": domain,
        "ratio": torrent.ratio,
        "seeding_seconds": torrent.seeding_seconds,
        "policy": None,
    }
    if policy is None:
        return False, "TRACKER_POLICY_MISSING", f"No policy exists for {domain}", evidence
    evidence["policy"] = {
        "minimum_ratio": policy.minimum_ratio,
        "minimum_seed_seconds": policy.minimum_seed_seconds,
        "combination": policy.combination,
        "grace_period_seconds": policy.grace_period_seconds,
        "automatic_deletion_allowed": policy.automatic_deletion_allowed,
    }
    if policy.combination == "NEVER_REMOVE":
        return False, "TRACKER_NEVER_REMOVE", f"{domain} is marked Never remove", evidence
    ratio_required = policy.combination in {
        "RATIO_ONLY",
        "RATIO_OR_TIME",
        "RATIO_AND_TIME",
    }
    time_required = policy.combination in {
        "TIME_ONLY",
        "RATIO_OR_TIME",
        "RATIO_AND_TIME",
    }
    if (ratio_required and policy.minimum_ratio is None) or (
        time_required and policy.minimum_seed_seconds is None
    ):
        return (
            False,
            "TRACKER_POLICY_INCOMPLETE",
            f"{domain} has an incomplete tracker policy",
            evidence,
        )

    ratio_met = torrent.ratio is not None and (
        policy.minimum_ratio is None or torrent.ratio >= policy.minimum_ratio
    )
    time_met = torrent.seeding_seconds is not None and (
        policy.minimum_seed_seconds is None
        or torrent.seeding_seconds >= policy.minimum_seed_seconds
    )
    combination_met = {
        "RATIO_ONLY": ratio_met,
        "TIME_ONLY": time_met,
        "RATIO_OR_TIME": ratio_met or time_met,
        "RATIO_AND_TIME": ratio_met and time_met,
    }.get(policy.combination, False)
    if not combination_met:
        if policy.combination == "RATIO_ONLY":
            reason = (
                f"{domain} ratio {torrent.ratio or 0:.2f} / "
                f"required {policy.minimum_ratio:.2f}"
            )
        elif policy.combination == "TIME_ONLY":
            reason = (
                f"{domain} seeded {_hours(torrent.seeding_seconds)} / required "
                f"{_hours(policy.minimum_seed_seconds)}"
            )
        else:
            joiner = "either" if policy.combination == "RATIO_OR_TIME" else "both"
            reason = (
                f"{domain} requires {joiner}: ratio {torrent.ratio or 0:.2f} / "
                f"{policy.minimum_ratio:.2f}, seeded {_hours(torrent.seeding_seconds)} / "
                f"{_hours(policy.minimum_seed_seconds)}"
            )
        return False, "TRACKER_REQUIREMENT_NOT_MET", reason, evidence
    if (
        torrent.seeding_seconds is None
        or torrent.seeding_seconds < policy.grace_period_seconds
    ):
        return (
            False,
            "TRACKER_GRACE_PERIOD",
            f"{domain} grace period: seeded {_hours(torrent.seeding_seconds)} / required "
            f"{_hours(policy.grace_period_seconds)}",
            evidence,
        )
    if not policy.automatic_deletion_allowed:
        return (
            False,
            "TRACKER_AUTOMATION_DISABLED",
            f"{domain} allows manual review only; automatic deletion is disabled",
            evidence,
        )
    return True, "TRACKER_REQUIREMENT_MET", f"{domain} tracker requirements are met", evidence


def evaluate_dry_run(session: Session) -> DryRunSummary:
    session.flush()
    now = utc_now()
    records = session.execute(
        select(MediaLifecycle, MediaIdentity, IntegrationInstance)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .join(IntegrationInstance, IntegrationInstance.id == MediaLifecycle.integration_id)
        .where(MediaLifecycle.state == "ACTIVE")
        .order_by(MediaIdentity.canonical_title, MediaIdentity.season_number)
    ).all()
    mappings = session.scalars(
        select(TorrentMediaMapping)
        .join(Torrent, TorrentMediaMapping.torrent_id == Torrent.id)
        .where(Torrent.present.is_(True))
    ).all()
    mappings_by_lifecycle: dict[str, list[TorrentMediaMapping]] = {}
    mappings_by_torrent: dict[str, list[TorrentMediaMapping]] = {}
    mapping_count_by_torrent: dict[str, int] = {}
    for mapping in mappings:
        mappings_by_lifecycle.setdefault(mapping.lifecycle_id, []).append(mapping)
        mappings_by_torrent.setdefault(mapping.torrent_id, []).append(mapping)
        mapping_count_by_torrent[mapping.torrent_id] = (
            mapping_count_by_torrent.get(mapping.torrent_id, 0) + 1
        )
    identities_by_lifecycle = {lifecycle.id: identity for lifecycle, identity, _ in records}
    torrents = {torrent.id: torrent for torrent in session.scalars(select(Torrent)).all()}
    trackers_by_torrent: dict[str, list[TorrentTracker]] = {}
    for tracker in session.scalars(select(TorrentTracker)).all():
        trackers_by_torrent.setdefault(tracker.torrent_id, []).append(tracker)
    policies = {
        policy.normalized_domain: policy
        for policy in session.scalars(
            select(TrackerPolicy).where(TrackerPolicy.selected.is_(True))
        ).all()
    }
    proposals = {
        proposal.lifecycle_id: proposal
        for proposal in session.scalars(select(DryRunProposal)).all()
    }

    current_lifecycle_ids: set[str] = set()
    counts = {"ELIGIBLE": 0, "BLOCKED": 0, "RETAINED": 0, "PROTECTED": 0}
    estimated_bytes = 0
    for lifecycle, identity, integration in records:
        current_lifecycle_ids.add(lifecycle.id)
        state = "BLOCKED"
        code = "SOURCE_EVIDENCE_BLOCKED"
        reason = lifecycle.decision_reason
        torrent_evidence: list[dict[str, object]] = []

        if integration.management_mode != "MANAGED":
            state, code = "PROTECTED", "INSTANCE_NOT_MANAGED"
            reason = f"{integration.name} is {integration.management_mode or 'not managed'}"
        elif lifecycle.protection_state == "PROTECTED":
            state, code = "PROTECTED", "MEDIA_PROTECTED"
        elif lifecycle.decision == "KEEP_RETAINED":
            state, code = "RETAINED", "RETENTION_ACTIVE"
        elif lifecycle.decision != "REVIEW_ELIGIBLE":
            state, code = "BLOCKED", f"LIFECYCLE_{lifecycle.decision}"
        else:
            lifecycle_mappings = mappings_by_lifecycle.get(lifecycle.id, [])
            if not lifecycle_mappings:
                state, code = "ELIGIBLE", "DRY_RUN_ELIGIBLE_NO_TORRENT"
                reason = (
                    "Retention elapsed and no current qBittorrent torrent is associated; "
                    "tracker rules do not apply"
                )
            elif any(
                mapping.confidence not in CONFIDENT_MAPPINGS for mapping in lifecycle_mappings
            ):
                code, reason = (
                    "TORRENT_MAPPING_UNCERTAIN",
                    "At least one torrent mapping needs administrator confirmation",
                )
            elif any(
                mapping_count_by_torrent[mapping.torrent_id] > 1
                for mapping in lifecycle_mappings
            ):
                shared_titles = sorted(
                    {
                        identities_by_lifecycle[other.lifecycle_id].canonical_title
                        for mapping in lifecycle_mappings
                        for other in mappings_by_torrent[mapping.torrent_id]
                        if other.lifecycle_id != lifecycle.id
                        and other.lifecycle_id in identities_by_lifecycle
                    }
                )
                if shared_titles:
                    visible_titles = shared_titles[:3]
                    shared_label = ", ".join(visible_titles)
                    if len(shared_titles) > len(visible_titles):
                        shared_label += f", and {len(shared_titles) - len(visible_titles)} more"
                    reason = (
                        f"A torrent also maps to {shared_label}; automatic cleanup is blocked"
                    )
                else:
                    reason = (
                        "A torrent maps to another media item; automatic cleanup is blocked"
                    )
                code, reason = (
                    "SHARED_TORRENT_MAPPING",
                    reason,
                )
            else:
                tracker_failure: tuple[str, str] | None = None
                for mapping in lifecycle_mappings:
                    torrent = torrents.get(mapping.torrent_id)
                    if torrent is None or not torrent.present:
                        tracker_failure = (
                            "TORRENT_NOT_PRESENT",
                            "A mapped torrent is no longer present in qBittorrent",
                        )
                        break
                    torrent_snapshot: dict[str, object] = {
                        "info_hash": torrent.info_hash,
                        "name": torrent.name,
                        "ratio": torrent.ratio,
                        "seeding_seconds": torrent.seeding_seconds,
                        "amount_left": torrent.amount_left,
                        "mapping_source": mapping.mapping_source,
                        "mapping_confidence": mapping.confidence,
                        "trackers": [],
                    }
                    torrent_evidence.append(torrent_snapshot)
                    if torrent.amount_left not in (None, 0):
                        tracker_failure = (
                            "TORRENT_INCOMPLETE",
                            f"{torrent.name} still has data left to download",
                        )
                        break
                    trackers = trackers_by_torrent.get(torrent.id, [])
                    if not trackers:
                        tracker_failure = (
                            "TRACKER_EVIDENCE_MISSING",
                            f"{torrent.name} has no tracker evidence",
                        )
                        break
                    for tracker in trackers:
                        domain = normalize_tracker_domain(tracker.host)
                        passed, tracker_code, tracker_reason, evidence = _tracker_result(
                            torrent, tracker, policies.get(domain)
                        )
                        tracker_list = torrent_snapshot["trackers"]
                        assert isinstance(tracker_list, list)
                        tracker_list.append(evidence)
                        if not passed:
                            tracker_failure = (tracker_code, tracker_reason)
                            break
                    if tracker_failure:
                        break
                if tracker_failure:
                    code, reason = tracker_failure
                else:
                    state, code = "ELIGIBLE", "DRY_RUN_ELIGIBLE"
                    reason = (
                        "Retention elapsed, mappings are confident, and all tracker rules are met"
                    )

        snapshot = {
            "evaluated_at": now.astimezone(UTC).isoformat(),
            "media": {
                "title": identity.canonical_title,
                "media_type": identity.media_type,
                "season_number": identity.season_number,
                "source": integration.name,
                "retention_deadline": (
                    lifecycle.retention_deadline.astimezone(UTC).isoformat()
                    if lifecycle.retention_deadline and lifecycle.retention_deadline.tzinfo
                    else lifecycle.retention_deadline.isoformat()
                    if lifecycle.retention_deadline
                    else None
                ),
                "protection_state": lifecycle.protection_state,
                "lifecycle_decision": lifecycle.decision,
            },
            "torrents": torrent_evidence,
            "external_mutations": [],
        }
        proposal = proposals.get(lifecycle.id)
        if proposal is None:
            proposal = DryRunProposal(lifecycle_id=lifecycle.id)
            session.add(proposal)
        proposal.state = state
        proposal.reason_code = code
        proposal.reason_text = reason
        proposal.estimated_bytes = max(0, lifecycle.current_size or 0)
        proposal.eligibility_snapshot = snapshot
        proposal.evaluated_at = now
        counts[state] += 1
        if state == "ELIGIBLE":
            estimated_bytes += proposal.estimated_bytes

    if current_lifecycle_ids:
        session.execute(
            delete(DryRunProposal).where(
                DryRunProposal.lifecycle_id.not_in(current_lifecycle_ids)
            )
        )
    else:
        session.execute(delete(DryRunProposal))
    for integration in session.scalars(
        select(IntegrationInstance).where(
            IntegrationInstance.enabled.is_(True),
            IntegrationInstance.kind.in_({"RADARR", "SONARR", "QBITTORRENT"}),
        )
    ):
        integration.dry_run_evaluated_at = now
    session.flush()
    return DryRunSummary(
        evaluated=len(records),
        eligible=counts["ELIGIBLE"],
        blocked=counts["BLOCKED"],
        retained=counts["RETAINED"],
        protected=counts["PROTECTED"],
        estimated_bytes=estimated_bytes,
        evaluated_at=now,
    )
