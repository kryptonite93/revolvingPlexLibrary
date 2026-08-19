from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.persistence.models import (
    DryRunProposal,
    IntegrationInstance,
    MediaIdentity,
    MediaLifecycle,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
    TrackerPolicy,
)
from app.services.dry_run import evaluate_dry_run
from app.services.inventory import sync_qbittorrent


def csrf_from(response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def authenticate(client) -> None:
    setup = client.get("/setup")
    response = client.post(
        "/setup",
        data={
            "username": "owner",
            "password": "a-secure-password",
            "password_confirm": "a-secure-password",
            "csrf": csrf_from(setup),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def add_candidate(session, *, tracker_domain: str = "tracker.example") -> MediaLifecycle:
    arr = IntegrationInstance(
        kind="RADARR",
        name="Radarr",
        base_url="http://radarr:7878",
        enabled=True,
        management_mode="MANAGED",
        credentials_encrypted="encrypted",
    )
    qbit = IntegrationInstance(
        kind="QBITTORRENT",
        name="qBittorrent",
        base_url="http://qbittorrent:8080",
        enabled=True,
        credentials_encrypted="encrypted",
    )
    identity = MediaIdentity(
        media_type="MOVIE",
        source_key="radarr:1",
        canonical_title="Dry Run Movie",
    )
    session.add_all([arr, qbit, identity])
    session.flush()
    lifecycle = MediaLifecycle(
        identity_id=identity.id,
        integration_id=arr.id,
        arr_item_id=1,
        state="ACTIVE",
        first_imported_at=datetime.now(UTC) - timedelta(days=200),
        retention_deadline=datetime.now(UTC) - timedelta(days=10),
        protection_state="UNPROTECTED",
        decision="REVIEW_ELIGIBLE",
        decision_reason="Retention elapsed",
        current_size=10_000,
    )
    torrent = Torrent(
        integration_id=qbit.id,
        info_hash="abc123",
        name="Dry Run Movie",
        amount_left=0,
        ratio=1.5,
        seeding_seconds=11 * 86_400,
        present=True,
    )
    session.add_all([lifecycle, torrent])
    session.flush()
    session.add_all(
        [
            TorrentMediaMapping(
                torrent_id=torrent.id,
                lifecycle_id=lifecycle.id,
                mapping_source="ARR_DOWNLOAD_ID",
                confidence="EXACT",
            ),
            TorrentTracker(
                torrent_id=torrent.id,
                url=f"https://{tracker_domain}/announce",
                host=tracker_domain,
            ),
        ]
    )
    session.flush()
    return lifecycle


def test_dry_run_blocks_unknown_tracker_policy(app) -> None:
    with app.state.database.session_factory() as session:
        lifecycle = add_candidate(session)
        summary = evaluate_dry_run(session)
        proposal = session.scalar(
            select(DryRunProposal).where(DryRunProposal.lifecycle_id == lifecycle.id)
        )

        assert summary.eligible == 0
        assert summary.blocked == 1
        assert proposal is not None
        assert proposal.reason_code == "TRACKER_POLICY_MISSING"
        assert proposal.eligibility_snapshot["external_mutations"] == []


def test_qbittorrent_sync_stores_actual_seeding_duration(app) -> None:
    with app.state.database.session_factory() as session:
        qbit = IntegrationInstance(
            kind="QBITTORRENT",
            name="qBittorrent",
            base_url="http://qbittorrent:8080",
            enabled=True,
            credentials_encrypted="encrypted",
        )
        session.add(qbit)
        session.flush()
        sync_qbittorrent(
            session,
            qbit,
            {"api_key": "unused"},
            rows=[
                {
                    "hash": "ABC123",
                    "name": "Seeded torrent",
                    "ratio": 1.25,
                    "seeding_time": 456_789,
                    "amount_left": 0,
                    "trackers": [
                        {
                            "url": "https://tracker.example/announce",
                            "status": 2,
                        }
                    ],
                }
            ],
        )
        torrent = session.scalar(select(Torrent))

        assert torrent is not None
        assert torrent.seeding_seconds == 456_789


def test_dry_run_marks_candidate_eligible_only_when_every_tracker_rule_passes(app) -> None:
    with app.state.database.session_factory() as session:
        lifecycle = add_candidate(session)
        policy = TrackerPolicy(
            normalized_domain="tracker.example",
            minimum_ratio=1.0,
            minimum_seed_seconds=10 * 86_400,
            combination="RATIO_AND_TIME",
            grace_period_seconds=12 * 3600,
            automatic_deletion_allowed=True,
        )
        session.add(policy)
        summary = evaluate_dry_run(session)
        proposal = session.scalar(
            select(DryRunProposal).where(DryRunProposal.lifecycle_id == lifecycle.id)
        )

        assert summary.eligible == 1, proposal.reason_text if proposal else "missing proposal"
        assert summary.estimated_bytes == 10_000
        assert proposal is not None
        assert proposal.state == "ELIGIBLE"
        assert proposal.reason_code == "DRY_RUN_ELIGIBLE"

        policy.automatic_deletion_allowed = False
        summary = evaluate_dry_run(session)
        assert summary.eligible == 0
        assert proposal.state == "BLOCKED"
        assert proposal.reason_code == "TRACKER_AUTOMATION_DISABLED"


def test_tracker_policy_settings_are_discovered_saved_and_re_evaluated(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        add_candidate(session, tracker_domain="private.example")
        session.commit()

    page = client.get("/settings")
    assert "Tracker rules" in page.text
    assert "private.example" in page.text
    assert "No trackers selected" in page.text
    assert 'name="domain" value="private.example"' not in page.text
    selection = client.post(
        "/settings/tracker-selection",
        data={
            "csrf": csrf_from(page),
            "domains": "private.example",
        },
        follow_redirects=True,
    )

    assert selection.status_code == 200
    assert "Tracker selection saved. 1 rule shown." in selection.text
    assert 'name="domain" value="private.example"' in selection.text
    response = client.post(
        "/settings/tracker-policy",
        data={
            "csrf": csrf_from(selection),
            "domain": "private.example",
            "combination": "RATIO_OR_TIME",
            "minimum_ratio": "1.0",
            "minimum_seed_days": "10",
            "grace_hours": "12",
            "automatic_deletion_allowed": "yes",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Tracker policy for private.example saved" in response.text
    assert "Read-only deletion preview" in response.text
    assert "Eligible in simulation" in response.text
    with app.state.database.session_factory() as session:
        policy = session.scalar(
            select(TrackerPolicy).where(
                TrackerPolicy.normalized_domain == "private.example"
            )
        )
        assert policy is not None
        assert policy.selected is True
        assert policy.minimum_seed_seconds == 10 * 86_400
        assert policy.automatic_deletion_allowed is True


def test_unselected_tracker_rule_is_hidden_and_blocks_dry_run(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        lifecycle = add_candidate(session, tracker_domain="private.example")
        lifecycle_id = lifecycle.id
        session.add(
            TrackerPolicy(
                normalized_domain="private.example",
                minimum_ratio=1.0,
                minimum_seed_seconds=10 * 86_400,
                combination="RATIO_AND_TIME",
                grace_period_seconds=12 * 3_600,
                automatic_deletion_allowed=True,
                selected=True,
            )
        )
        evaluate_dry_run(session)
        session.commit()

    page = client.get("/settings")
    assert 'name="domain" value="private.example"' in page.text
    response = client.post(
        "/settings/tracker-selection",
        data={"csrf": csrf_from(page)},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "Tracker selection saved. 0 rules shown." in response.text
    assert 'name="domain" value="private.example"' not in response.text
    assert "No trackers selected" in response.text
    with app.state.database.session_factory() as session:
        policy = session.scalar(
            select(TrackerPolicy).where(
                TrackerPolicy.normalized_domain == "private.example"
            )
        )
        proposal = session.scalar(
            select(DryRunProposal).where(DryRunProposal.lifecycle_id == lifecycle_id)
        )
        assert policy is not None
        assert policy.selected is False
        assert proposal is not None
        assert proposal.reason_code == "TRACKER_POLICY_MISSING"
