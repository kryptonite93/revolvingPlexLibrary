from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.persistence.models import (
    AdminUser,
    DeletionJob,
    DryRunProposal,
    IntegrationInstance,
    ManagedLibrary,
    MediaIdentity,
    MediaLifecycle,
    RolloutPolicy,
    SourceFreshness,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
    TrackerPolicy,
)
from app.security.auth import hash_password
from app.services.deletion_jobs import (
    DeletionBlocked,
    DeletionJobError,
    _live_revalidate,
    approve_movie_job,
    create_movie_job,
    execute_movie_job,
    retry_movie_reconciliation,
)


def seed_movie_case(app):
    now = datetime.now(UTC)
    with app.state.database.session_factory() as session:
        admin = AdminUser(username="owner", password_hash="unused")
        rollout = RolloutPolicy(id="default", mode="APPROVAL_REQUIRED")
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            active_management_enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "radarr"}),
            health_status="HEALTHY",
            last_success_at=now,
            full_sync_completed_at=now,
            dry_run_evaluated_at=now,
        )
        qbit = IntegrationInstance(
            kind="QBITTORRENT",
            name="qBittorrent",
            base_url="http://qbittorrent:8080",
            enabled=True,
            active_management_enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "qbit"}),
            health_status="HEALTHY",
            last_success_at=now,
            full_sync_completed_at=now,
            dry_run_evaluated_at=now,
        )
        plex = IntegrationInstance(
            kind="PLEX",
            name="Plex",
            base_url="http://plex:32400",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "plex"}),
            health_status="HEALTHY",
            last_success_at=now,
            full_sync_completed_at=now,
        )
        tautulli = IntegrationInstance(
            kind="TAUTULLI",
            name="Tautulli",
            base_url="http://tautulli:8181",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "tautulli"}),
            health_status="HEALTHY",
            last_success_at=now,
            full_sync_completed_at=now,
        )
        overseerr = IntegrationInstance(
            kind="OVERSEERR",
            name="Overseerr",
            base_url="http://overseerr:5055",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "overseerr"}),
            health_status="HEALTHY",
            last_success_at=now,
            full_sync_completed_at=now,
        )
        session.add_all([admin, rollout, radarr, qbit, plex, tautulli, overseerr])
        session.flush()
        library = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="1",
            name="Movies",
            media_type="movie",
            enabled=True,
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="radarr:42",
            tmdb_id=123,
            canonical_title="Disposable Test Movie",
            year=2020,
        )
        session.add_all([library, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr.id,
            arr_item_id=42,
            plex_rating_key="plex-42",
            library_id=library.id,
            state="ACTIVE",
            first_imported_at=now - timedelta(days=300),
            retention_deadline=now - timedelta(days=30),
            protection_state="UNPROTECTED",
            decision="REVIEW_ELIGIBLE",
            decision_reason="Retention elapsed",
            current_size=10_000,
        )
        torrent = Torrent(
            integration_id=qbit.id,
            info_hash="abc123",
            name="Disposable Test Movie",
            amount_left=0,
            ratio=2.0,
            seeding_seconds=20 * 86_400,
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
                    url="https://tracker.example/announce",
                    host="tracker.example",
                    status=2,
                ),
                TrackerPolicy(
                    normalized_domain="tracker.example",
                    minimum_ratio=1.0,
                    minimum_seed_seconds=10 * 86_400,
                    combination="RATIO_AND_TIME",
                    grace_period_seconds=0,
                    automatic_deletion_allowed=True,
                    selected=True,
                ),
                DryRunProposal(
                    lifecycle_id=lifecycle.id,
                    state="ELIGIBLE",
                    reason_code="DRY_RUN_ELIGIBLE",
                    reason_text="Every current safety rule passes",
                    estimated_bytes=10_000,
                    eligibility_snapshot={
                        "media": {
                            "title": identity.canonical_title,
                            "media_type": "MOVIE",
                            "lifecycle_decision": "REVIEW_ELIGIBLE",
                        },
                        "torrents": [{"info_hash": torrent.info_hash}],
                        "external_mutations": [],
                    },
                    evaluated_at=now,
                ),
            ]
        )
        for integration in (radarr, qbit, plex, tautulli, overseerr):
            session.add(
                SourceFreshness(
                    integration_id=integration.id,
                    source_kind=integration.kind,
                    status="FRESH",
                    stale_after_seconds=3600,
                    last_attempt_at=now,
                    last_success_at=now,
                )
            )
        session.commit()
        return {
            "admin_id": admin.id,
            "lifecycle_id": lifecycle.id,
            "title": identity.canonical_title,
        }


def install_fake_adapters(
    monkeypatch,
    *,
    plex_active: bool = False,
    plex_present: bool = False,
    radarr_delete_fails: bool = False,
    radarr_tmdb_id: int | None = 123,
    radarr_exclusion_created: bool = False,
    radarr_exclusion_fails: bool = False,
):
    calls: list[str] = []

    class FakeArr:
        present = True
        tmdb_id = radarr_tmdb_id

        def __init__(self, *_args, **_kwargs):
            pass

        def movie(self, _movie_id):
            calls.append("radarr-check")
            return (
                {"id": 42, "tmdbId": self.tmdb_id, "hasFile": True}
                if self.present
                else None
            )

        def delete_movie(self, _movie_id, *, add_import_exclusion):
            assert add_import_exclusion is True
            calls.append("radarr-delete")
            if radarr_delete_fails:
                raise OSError("Radarr refused the delete")
            self.__class__.present = False

        def ensure_import_exclusion(self, tmdb_id, title, year):
            assert tmdb_id == 123
            assert title == "Disposable Test Movie"
            assert year == 2020
            calls.append("radarr-exclusion")
            if radarr_exclusion_fails:
                raise OSError("Radarr refused the import exclusion")
            return radarr_exclusion_created

    class FakeQbit:
        present = True

        def __init__(self, *_args, **_kwargs):
            pass

        def torrent(self, _info_hash):
            calls.append("qbit-check")
            if not self.present:
                return None
            return {
                "hash": "abc123",
                "name": "Disposable Test Movie",
                "state": "stalledUP",
                "amount_left": 0,
                "ratio": 2.0,
                "seeding_time": 20 * 86_400,
                "trackers": [
                    {
                        "url": "https://tracker.example/announce",
                        "status": 2,
                    }
                ],
            }

        def delete_torrent(self, _info_hash, *, delete_files):
            assert delete_files is True
            calls.append("qbit-delete")
            self.__class__.present = False

    class FakePlex:
        present = plex_present

        def __init__(self, *_args, **_kwargs):
            pass

        def active_session_rating_keys(self):
            calls.append("plex-sessions")
            return {"plex-42"} if plex_active else set()

        def refresh_library(self, section_id):
            assert section_id == "1"
            calls.append("plex-refresh")

        def item_present(self, _rating_key):
            calls.append("plex-check")
            return self.present

    class FakeOverseerr:
        requestable = True

        def __init__(self, *_args, **_kwargs):
            pass

        def movie(self, tmdb_id):
            assert tmdb_id == 123
            calls.append("overseerr-check")
            return {"id": 123, "mediaInfo": None if self.requestable else {"status": 5}}

    monkeypatch.setattr("app.services.deletion_jobs.ArrAdapter", FakeArr)
    monkeypatch.setattr("app.services.deletion_jobs.QBittorrentAdapter", FakeQbit)
    monkeypatch.setattr("app.services.deletion_jobs.PlexAdapter", FakePlex)
    monkeypatch.setattr("app.services.deletion_jobs.OverseerrAdapter", FakeOverseerr)
    return calls, FakeArr, FakeQbit, FakePlex, FakeOverseerr


def prepare_and_approve(app, case):
    with app.state.database.session_factory() as session:
        job = create_movie_job(
            session,
            lifecycle_id=case["lifecycle_id"],
            admin_id=case["admin_id"],
        )
        approve_movie_job(
            session,
            job,
            admin_id=case["admin_id"],
            confirmation_title=case["title"],
        )
        return job.id


def login_client(app, client: TestClient, admin_id: str) -> None:
    with app.state.database.session_factory() as session:
        admin = session.get(AdminUser, admin_id)
        assert admin is not None
        admin.password_hash = hash_password("visualpass")
        session.commit()
    login_page = client.get("/login")
    login_csrf = re.search(r'name="csrf" value="([^"]+)"', login_page.text)
    assert login_csrf is not None
    response = client.post(
        "/login",
        data={
            "username": "owner",
            "password": "visualpass",
            "csrf": login_csrf.group(1),
        },
    )
    assert response.status_code == 200


def test_approval_is_local_until_separate_execution(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, *_fakes = install_fake_adapters(monkeypatch)

    job_id = prepare_and_approve(app, case)

    assert calls == []
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        assert job.state == "APPROVED"


def test_prepared_movie_executes_with_one_confirmation(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, *_fakes = install_fake_adapters(monkeypatch)
    with app.state.database.session_factory() as session:
        job = create_movie_job(
            session,
            lifecycle_id=case["lifecycle_id"],
            admin_id=case["admin_id"],
        )
        execute_movie_job(
            session,
            job,
            app.state.credential_cipher,
            admin_id=case["admin_id"],
        )

        assert job.state == "COMPLETED"
        assert job.approved_at is not None

    assert "radarr-delete" in calls


def test_manual_movie_execution_orders_radarr_before_torrent(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, *_fakes = install_fake_adapters(monkeypatch)
    job_id = prepare_and_approve(app, case)

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        execute_movie_job(
            session,
            job,
            app.state.credential_cipher,
            admin_id=case["admin_id"],
        )

    assert calls.index("radarr-delete") < calls.index("qbit-delete")
    assert calls.index("qbit-delete") < calls.index("plex-refresh")
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        lifecycle = session.get(MediaLifecycle, case["lifecycle_id"])
        assert job is not None and job.state == "COMPLETED"
        assert lifecycle is not None and lifecycle.state == "DELETED"


def test_active_plex_session_blocks_before_any_delete(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, *_fakes = install_fake_adapters(monkeypatch, plex_active=True)
    job_id = prepare_and_approve(app, case)

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        with pytest.raises(DeletionBlocked, match="currently playing"):
            execute_movie_job(
                session,
                job,
                app.state.credential_cipher,
                admin_id=case["admin_id"],
            )

    assert "radarr-delete" not in calls
    assert "qbit-delete" not in calls
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None and job.state == "BLOCKED"


def test_radarr_failure_never_calls_qbittorrent_delete(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, *_fakes = install_fake_adapters(monkeypatch, radarr_delete_fails=True)
    job_id = prepare_and_approve(app, case)

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        with pytest.raises(DeletionJobError, match="Radarr refused the delete"):
            execute_movie_job(
                session,
                job,
                app.state.credential_cipher,
                admin_id=case["admin_id"],
            )

    assert "radarr-delete" in calls
    assert "qbit-delete" not in calls
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        lifecycle = session.get(MediaLifecycle, case["lifecycle_id"])
        assert job is not None and job.state == "RADARR_DELETE_REQUESTED"
        assert lifecycle is not None and lifecycle.state == "ACTIVE"


def test_movie_without_current_torrent_skips_qbittorrent_delete(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    with app.state.database.session_factory() as session:
        torrent = session.scalar(select(Torrent))
        assert torrent is not None
        session.query(TorrentMediaMapping).filter(
            TorrentMediaMapping.torrent_id == torrent.id
        ).delete()
        session.query(TorrentTracker).filter(TorrentTracker.torrent_id == torrent.id).delete()
        session.delete(torrent)
        session.commit()

    calls, *_fakes = install_fake_adapters(monkeypatch)
    job_id = prepare_and_approve(app, case)
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        execute_movie_job(
            session,
            job,
            app.state.credential_cipher,
            admin_id=case["admin_id"],
        )
        assert job.state == "COMPLETED"
        assert job.current_step == "REQUESTABILITY_CONFIRMED"

    assert "radarr-delete" in calls
    assert "qbit-delete" not in calls


def test_reconciliation_can_finish_after_plex_scan_converges(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    _calls, _arr, _qbit, plex, _overseerr = install_fake_adapters(
        monkeypatch, plex_present=True
    )
    job_id = prepare_and_approve(app, case)

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        execute_movie_job(
            session,
            job,
            app.state.credential_cipher,
            admin_id=case["admin_id"],
        )
        assert job.state == "RECONCILE_REQUIRED"

    plex.present = False
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        retry_movie_reconciliation(
            session,
            job,
            app.state.credential_cipher,
            admin_id=case["admin_id"],
        )
        assert job.state == "COMPLETED"

    assert "radarr-exclusion" in _calls


def test_legacy_resume_repairs_exclusion_before_torrent_cleanup(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, radarr, *_fakes = install_fake_adapters(
        monkeypatch,
        radarr_exclusion_created=True,
    )
    job_id = prepare_and_approve(app, case)

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        _live_revalidate(session, job, app.state.credential_cipher)
        radarr.present = False
        job.state = "RADARR_DELETED"
        job.current_step = "LEGACY_RADARR_DELETE_CONFIRMED"
        session.commit()

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        execute_movie_job(
            session,
            job,
            app.state.credential_cipher,
            admin_id=case["admin_id"],
        )

    assert calls.index("radarr-exclusion") < calls.index("qbit-delete")


def test_legacy_exclusion_failure_prevents_torrent_cleanup(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, radarr, *_fakes = install_fake_adapters(
        monkeypatch,
        radarr_exclusion_fails=True,
    )
    job_id = prepare_and_approve(app, case)

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        _live_revalidate(session, job, app.state.credential_cipher)
        radarr.present = False
        job.state = "RADARR_DELETED"
        session.commit()

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        with pytest.raises(DeletionJobError, match="refused the import exclusion"):
            execute_movie_job(
                session,
                job,
                app.state.credential_cipher,
                admin_id=case["admin_id"],
            )

    assert "radarr-exclusion" in calls
    assert "qbit-delete" not in calls


def test_interrupted_radarr_resume_requires_a_verified_snapshot_tmdb_id(
    app, monkeypatch
) -> None:
    case = seed_movie_case(app)
    calls, *_fakes = install_fake_adapters(monkeypatch)
    job_id = prepare_and_approve(app, case)
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        job.state = "RADARR_DELETE_REQUESTED"
        job.current_step = "CALLING_RADARR_DELETE"
        job.execution_snapshot = {}
        session.commit()

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        with pytest.raises(DeletionBlocked, match="no live-verified TMDb ID"):
            execute_movie_job(
                session,
                job,
                app.state.credential_cipher,
                admin_id=case["admin_id"],
            )

    assert "radarr-delete" not in calls
    assert "qbit-delete" not in calls


def test_resumed_delete_blocks_if_live_radarr_identity_changed(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    calls, radarr, *_fakes = install_fake_adapters(monkeypatch)
    job_id = prepare_and_approve(app, case)
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        _live_revalidate(session, job, app.state.credential_cipher)
        assert job.execution_snapshot["tmdb_id"] == 123
        radarr.tmdb_id = 999

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        with pytest.raises(DeletionBlocked, match="different or missing TMDb identity"):
            execute_movie_job(
                session,
                job,
                app.state.credential_cipher,
                admin_id=case["admin_id"],
            )

    assert "radarr-delete" not in calls
    assert "qbit-delete" not in calls


def test_missing_tmdb_id_blocks_before_radarr_delete(app, monkeypatch) -> None:
    case = seed_movie_case(app)
    with app.state.database.session_factory() as session:
        identity = session.scalar(select(MediaIdentity))
        assert identity is not None
        identity.tmdb_id = None
        session.commit()

    calls, *_fakes = install_fake_adapters(monkeypatch, radarr_tmdb_id=None)
    job_id = prepare_and_approve(app, case)
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        with pytest.raises(DeletionBlocked, match="import exclusion cannot be guaranteed"):
            execute_movie_job(
                session,
                job,
                app.state.credential_cipher,
                admin_id=case["admin_id"],
            )

    assert "radarr-delete" not in calls
    assert "qbit-delete" not in calls


def test_protected_radarr_blocks_legacy_resume_before_torrent_cleanup(
    app, monkeypatch
) -> None:
    case = seed_movie_case(app)
    calls, radarr, *_fakes = install_fake_adapters(monkeypatch)
    job_id = prepare_and_approve(app, case)

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        _live_revalidate(session, job, app.state.credential_cipher)
        radarr.present = False
        job.state = "RADARR_DELETED"
        integration = session.scalar(
            select(IntegrationInstance).where(IntegrationInstance.kind == "RADARR")
        )
        assert integration is not None
        integration.management_mode = "PROTECTED"
        session.commit()

    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        with pytest.raises(DeletionBlocked, match="not Managed"):
            execute_movie_job(
                session,
                job,
                app.state.credential_cipher,
                admin_id=case["admin_id"],
            )

    assert "radarr-exclusion" not in calls
    assert "qbit-delete" not in calls


def test_execute_route_requires_only_the_confirmation_checkbox(
    app, client: TestClient, monkeypatch
) -> None:
    case = seed_movie_case(app)
    calls, *_fakes = install_fake_adapters(monkeypatch)
    with app.state.database.session_factory() as session:
        job = create_movie_job(
            session,
            lifecycle_id=case["lifecycle_id"],
            admin_id=case["admin_id"],
        )
        job_id = job.id
        session.commit()

    login_client(app, client, case["admin_id"])
    detail = client.get(f"/deletion-jobs/{job_id}")
    csrf = re.search(r'name="csrf" value="([^"]+)"', detail.text)
    assert csrf is not None
    assert 'name="confirm_title"' not in detail.text
    assert 'name="confirm_phrase"' not in detail.text
    assert detail.text.count('name="acknowledge"') == 1

    missing_confirmation = client.post(
        f"/deletion-jobs/{job_id}/execute",
        data={"csrf": csrf.group(1)},
        follow_redirects=False,
    )
    assert missing_confirmation.status_code == 303
    assert calls == []

    executed = client.post(
        f"/deletion-jobs/{job_id}/execute",
        data={"csrf": csrf.group(1), "acknowledge": "yes"},
        follow_redirects=False,
    )
    assert executed.status_code == 303
    assert "radarr-delete" in calls


def test_revalidated_checkpoint_renders_a_resume_confirmation(
    app, client: TestClient, monkeypatch
) -> None:
    case = seed_movie_case(app)
    install_fake_adapters(monkeypatch)
    job_id = prepare_and_approve(app, case)
    with app.state.database.session_factory() as session:
        job = session.get(DeletionJob, job_id)
        assert job is not None
        _live_revalidate(session, job, app.state.credential_cipher)
        assert job.state == "REVALIDATED"

    login_client(app, client, case["admin_id"])
    detail = client.get(f"/deletion-jobs/{job_id}")

    assert detail.status_code == 200
    assert 'name="acknowledge"' in detail.text
    assert "Resume deletion" in detail.text
