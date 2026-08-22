from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.persistence.models import (
    AdminUser,
    IntegrationInstance,
    IntegrationLibraryMapping,
    InventoryPolicy,
    ManagedLibrary,
    ManualDeletionBatch,
    ManualDeletionItem,
    MediaIdentity,
    MediaLifecycle,
    Playback,
    RequesterProfile,
    RequestRecord,
    RolloutPolicy,
    SourceFreshness,
)
from app.services.inventory import sync_tautulli
from app.services.manual_management import (
    create_manual_batch,
    execute_manual_batch,
    resolve_manual_selection,
)
from app.services.sync_coordinator import SyncActivity, SyncAlreadyRunning


def csrf_from(response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def authenticate(client) -> None:
    setup = client.get("/setup")
    client.post(
        "/setup",
        data={
            "username": "owner",
            "password": "a-secure-password",
            "password_confirm": "a-secure-password",
            "csrf": csrf_from(setup),
        },
    )


def test_manual_management_links_one_requester_into_other_arr_instances(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        overseerr = IntegrationInstance(
            kind="OVERSEERR",
            name="Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "request"}),
        )
        radarr_4k = IntegrationInstance(
            kind="RADARR",
            name="Radarr 4K",
            base_url="http://radarr-4k:7878",
            enabled=True,
            active_management_enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "radarr"}),
        )
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Sonarr 1080p",
            base_url="http://sonarr:8989",
            enabled=True,
            active_management_enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "sonarr"}),
        )
        session.add_all([overseerr, radarr_4k, sonarr])
        session.flush()
        requester = RequesterProfile(
            integration_id=overseerr.id,
            external_id="9",
            username="viewer",
            display_name="Viewer Name",
        )
        movie = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:321",
            tmdb_id=321,
            canonical_title="Cross Instance Movie",
            year=2026,
        )
        season_one = MediaIdentity(
            media_type="SEASON",
            source_key="tvdb:100:season:1",
            tvdb_id=100,
            series_tvdb_id=100,
            season_number=1,
            canonical_title="Requested Show · Season 1",
        )
        season_two = MediaIdentity(
            media_type="SEASON",
            source_key="tvdb:100:season:2",
            tvdb_id=100,
            series_tvdb_id=100,
            season_number=2,
            canonical_title="Requested Show · Season 2",
        )
        session.add_all([requester, movie, season_one, season_two])
        session.flush()
        session.add_all(
            [
                RequestRecord(
                    integration_id=overseerr.id,
                    external_request_id=1,
                    media_type="movie",
                    tmdb_id=321,
                    status="2",
                    requester_id="9",
                    present=True,
                ),
                RequestRecord(
                    integration_id=overseerr.id,
                    external_request_id=2,
                    media_type="tv",
                    tvdb_id=100,
                    status="2",
                    requester_id="9",
                    present=True,
                ),
                MediaLifecycle(
                    identity_id=movie.id,
                    integration_id=radarr_4k.id,
                    arr_item_id=44,
                    state="ACTIVE",
                    current_size=10_000,
                    last_meaningful_watch_at=datetime(2026, 1, 2, tzinfo=UTC),
                    protection_state="PROTECTED",
                    protection_sources=["MANUAL_SELECTION"],
                ),
                MediaLifecycle(
                    identity_id=season_one.id,
                    integration_id=sonarr.id,
                    arr_item_id=7,
                    state="ACTIVE",
                    current_size=20_000,
                    last_meaningful_watch_at=datetime(2026, 2, 3, tzinfo=UTC),
                ),
                MediaLifecycle(
                    identity_id=season_two.id,
                    integration_id=sonarr.id,
                    arr_item_id=7,
                    state="ACTIVE",
                    current_size=30_000,
                ),
            ]
        )
        session.commit()
        requester_id = requester.id
        radarr_id = radarr_4k.id
        sonarr_id = sonarr.id

    movie_page = client.get(
        "/manual-management",
        params={"requester": requester_id, "instance": radarr_id, "tracker": "ALL"},
    )
    assert movie_page.status_code == 200
    assert "Cross Instance Movie" in movie_page.text
    assert "Radarr 4K" in movie_page.text
    assert "Protected" in movie_page.text
    assert "Meaningful watch" in movie_page.text
    assert "All watch history" in movie_page.text
    assert "Add movies to this Radarr instance’s exclusion list" in movie_page.text

    television_page = client.get(
        "/manual-management",
        params={"requester": requester_id, "instance": sonarr_id, "tracker": "ALL"},
    )
    assert television_page.status_code == 200
    assert "Requested Show" in television_page.text
    assert "Season 1" in television_page.text
    assert "Season 2" in television_page.text
    assert "Season exclusions are unavailable" in television_page.text

    watched_page = client.get(
        "/manual-management",
        params={
            "requester": requester_id,
            "instance": sonarr_id,
            "tracker": "ALL",
            "watch": "WATCHED",
        },
    )
    assert watched_page.status_code == 200
    assert "Season 1" in watched_page.text
    assert "Season 2" not in watched_page.text
    assert '<option value="WATCHED" selected>Meaningfully watched</option>' in watched_page.text

    never_watched_page = client.get(
        "/manual-management",
        params={
            "requester": requester_id,
            "instance": sonarr_id,
            "tracker": "ALL",
            "watch": "NEVER_WATCHED",
        },
    )
    assert never_watched_page.status_code == 200
    assert "Season 1" not in never_watched_page.text
    assert "Season 2" in never_watched_page.text
    assert "Never meaningfully watched" in never_watched_page.text


def test_coordinator_conflict_leaves_a_visible_batch_retry(client, app, monkeypatch) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        admin = session.scalar(select(AdminUser))
        rollout = session.get(RolloutPolicy, "default")
        assert admin is not None
        if rollout is None:
            rollout = RolloutPolicy(id="default", mode="APPROVAL_REQUIRED")
            session.add(rollout)
        else:
            rollout.mode = "APPROVAL_REQUIRED"
        overseerr = IntegrationInstance(
            kind="OVERSEERR",
            name="Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "request"}),
        )
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            active_management_enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "radarr"}),
        )
        session.add_all([overseerr, radarr])
        session.flush()
        requester = RequesterProfile(
            integration_id=overseerr.id,
            external_id="9",
            display_name="Viewer",
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:321",
            tmdb_id=321,
            canonical_title="Queued Movie",
        )
        session.add_all([requester, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr.id,
            arr_item_id=44,
            state="ACTIVE",
            current_size=10_000,
        )
        session.add_all(
            [
                lifecycle,
                RequestRecord(
                    integration_id=overseerr.id,
                    external_request_id=1,
                    media_type="movie",
                    tmdb_id=321,
                    status="2",
                    requester_id="9",
                    present=True,
                ),
            ]
        )
        session.commit()
        requester_id = requester.id
        radarr_id = radarr.id
        lifecycle_id = lifecycle.id

    def busy(*_args, **_kwargs):
        raise SyncAlreadyRunning(
            SyncActivity("other", "Another sync", "manual", datetime.now(UTC))
        )

    monkeypatch.setattr(app.state.sync_coordinator, "acquire", busy)
    page = client.get(
        "/manual-management",
        params={"requester": requester_id, "instance": radarr_id, "tracker": "ALL"},
    )
    response = client.post(
        "/manual-management/execute",
        data={
            "requester_profile_id": requester_id,
            "integration_id": radarr_id,
            "tracker_filter": "ALL",
            "lifecycle_ids": lifecycle_id,
            "add_import_exclusion": "yes",
            "acknowledge": "yes",
            "csrf": csrf_from(page),
        },
    )

    assert response.status_code == 200
    assert "Retry unfinished steps" in response.text
    assert "Retry this batch later" in response.text
    with app.state.database.session_factory() as session:
        batch = session.scalar(select(ManualDeletionBatch))
        assert batch is not None
        assert batch.state == "PENDING"
        assert batch.completed_items == 0
        assert batch.total_items == 1


@pytest.mark.parametrize(
    ("plex_rating_key", "season_number", "plex_fresh", "qbit_fresh", "expected_error"),
    [
        ("season-2", 2, True, None, None),
        (
            None,
            2,
            True,
            None,
            "does not have an exact Plex identity",
        ),
        ("season-2", None, True, None, "no verified Sonarr season number"),
        ("season-2", 2, False, None, "Synchronize first: Plex"),
        ("season-2", 2, True, False, "Synchronize first: qBittorrent"),
    ],
)
def test_manual_sonarr_batch_deletes_one_season_and_unmonitors_it(
    app,
    monkeypatch,
    plex_rating_key,
    season_number,
    plex_fresh,
    qbit_fresh,
    expected_error,
) -> None:
    now = datetime.now(UTC)
    with app.state.database.session_factory() as session:
        admin = AdminUser(username="owner", password_hash="unused")
        rollout = RolloutPolicy(id="default", mode="APPROVAL_REQUIRED")
        overseerr = IntegrationInstance(
            kind="OVERSEERR",
            name="Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            health_status="HEALTHY",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "request"}),
        )
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Sonarr 1080p",
            base_url="http://sonarr:8989",
            enabled=True,
            active_management_enabled=True,
            management_mode="MANAGED",
            health_status="HEALTHY",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "sonarr"}),
        )
        plex = IntegrationInstance(
            kind="PLEX",
            name="Plex",
            base_url="http://plex:32400",
            enabled=True,
            health_status="HEALTHY",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "plex"}),
        )
        session.add_all([admin, rollout, overseerr, sonarr, plex])
        qbittorrent = None
        if qbit_fresh is not None:
            qbittorrent = IntegrationInstance(
                kind="QBITTORRENT",
                name="qBittorrent",
                base_url="http://qbittorrent:8080",
                enabled=True,
                active_management_enabled=True,
                health_status="HEALTHY",
                credentials_encrypted=app.state.credential_cipher.encrypt(
                    {"api_key": "qbittorrent"}
                ),
            )
            session.add(qbittorrent)
        session.flush()
        requester = RequesterProfile(
            integration_id=overseerr.id,
            external_id="9",
            display_name="Viewer Name",
        )
        library = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="2",
            name="Television",
            media_type="show",
            enabled=True,
        )
        identity = MediaIdentity(
            media_type="SEASON",
            source_key="tvdb:100:season:2",
            tvdb_id=100,
            series_tvdb_id=100,
            season_number=season_number,
            canonical_title="The Show · Season 2",
        )
        session.add_all([requester, library, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=sonarr.id,
            arr_item_id=7,
            library_id=library.id,
            plex_rating_key=plex_rating_key,
            state="ACTIVE",
            monitored=True,
            current_size=1000,
            protection_state="PROTECTED",
            protection_sources=["PROTECTED_REQUESTER"],
        )
        session.add_all(
            [
                lifecycle,
                IntegrationLibraryMapping(
                    integration_id=sonarr.id,
                    library_id=library.id,
                    source="MANUAL",
                ),
                RequestRecord(
                    integration_id=overseerr.id,
                    external_request_id=2,
                    media_type="tv",
                    tvdb_id=100,
                    status="2",
                    requester_id="9",
                    present=True,
                ),
                SourceFreshness(
                    integration_id=overseerr.id,
                    source_kind="OVERSEERR",
                    status="FRESH",
                    stale_after_seconds=3600,
                    last_success_at=now,
                ),
                SourceFreshness(
                    integration_id=sonarr.id,
                    source_kind="SONARR",
                    status="FRESH",
                    stale_after_seconds=3600,
                    last_success_at=now,
                ),
                SourceFreshness(
                    integration_id=plex.id,
                    source_kind="PLEX",
                    status="FRESH" if plex_fresh else "NEVER_SYNCED",
                    stale_after_seconds=3600,
                    last_success_at=now if plex_fresh else None,
                ),
            ]
        )
        if qbittorrent is not None:
            session.add(
                SourceFreshness(
                    integration_id=qbittorrent.id,
                    source_kind="QBITTORRENT",
                    status="FRESH" if qbit_fresh else "NEVER_SYNCED",
                    stale_after_seconds=900,
                    last_success_at=now if qbit_fresh else None,
                )
            )
        session.commit()

        files = [{"id": 21, "seriesId": 7, "seasonNumber": 2}]
        deleted_ids: list[int] = []
        monitoring_updates: list[tuple[int, int, bool]] = []
        plex_refreshes: list[str] = []
        monkeypatch.setattr(
            "app.services.manual_management.ArrAdapter.series",
            lambda _adapter, _series_id: {
                "id": 7,
                "tvdbId": 100,
                "seasons": [{"seasonNumber": 2, "monitored": True}],
            },
        )
        monkeypatch.setattr(
            "app.services.manual_management.ArrAdapter.episode_files",
            lambda _adapter, _series_id: list(files),
        )

        def delete_files(_adapter, episode_file_ids):
            deleted_ids.extend(episode_file_ids)
            files.clear()

        monkeypatch.setattr(
            "app.services.manual_management.ArrAdapter.delete_episode_files", delete_files
        )
        monkeypatch.setattr(
            "app.services.manual_management.ArrAdapter.set_season_monitored",
            lambda _adapter, series_id, season_number, *, monitored: monitoring_updates.append(
                (series_id, season_number, monitored)
            ),
        )
        monkeypatch.setattr(
            "app.services.manual_management.PlexAdapter.active_session_rating_keys",
            lambda _adapter: set(),
        )
        monkeypatch.setattr(
            "app.services.manual_management.PlexAdapter.refresh_library",
            lambda _adapter, section_id: plex_refreshes.append(section_id),
        )

        profile, target, candidates = resolve_manual_selection(
            session,
            requester_profile_id=requester.id,
            integration_id=sonarr.id,
            tracker_filter="ALL",
            watch_filter="ALL",
            lifecycle_ids=[lifecycle.id],
            select_all_filtered=False,
        )
        batch = create_manual_batch(
            session,
            profile=profile,
            integration=target,
            candidates=candidates,
            admin_id=admin.id,
            add_import_exclusion=True,
        )
        execute_manual_batch(
            session,
            batch,
            app.state.credential_cipher,
            admin_id=admin.id,
        )

        session.refresh(lifecycle)
        session.refresh(batch)
        if expected_error is not None:
            item = session.scalar(
                select(ManualDeletionItem).where(ManualDeletionItem.batch_id == batch.id)
            )
            assert item is not None
            assert batch.state == "ATTENTION_REQUIRED"
            assert batch.completed_items == 0
            assert expected_error in (item.last_error or "")
            assert deleted_ids == []
            assert monitoring_updates == []
            assert plex_refreshes == []
            assert lifecycle.state == "ACTIVE"
            return
        assert batch.state == "COMPLETED"
        assert batch.completed_items == 1
        assert batch.add_import_exclusion is False
        assert deleted_ids == [21]
        assert monitoring_updates == [(7, 2, False)]
        assert plex_refreshes == ["2"]
        assert lifecycle.state == "DELETED"
        assert lifecycle.monitored is False


def test_tautulli_sync_backfills_existing_playback_user_names(app, monkeypatch) -> None:
    with app.state.database.session_factory() as session:
        tautulli = IntegrationInstance(
            kind="TAUTULLI",
            name="Tautulli",
            base_url="http://tautulli:8181",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        policy = InventoryPolicy(id="default")
        playback = Playback(
            integration_id="pending",
            external_row_id="1",
            media_type="movie",
            watched_at=datetime.now(UTC),
            meaningful=True,
            user_id="7",
        )
        session.add_all([tautulli, policy])
        session.flush()
        playback.integration_id = tautulli.id
        session.add(playback)
        monkeypatch.setattr(
            "app.services.inventory.fetch_tautulli_history",
            lambda *_args, **_kwargs: ([], 1),
        )
        monkeypatch.setattr(
            "app.services.inventory.TautulliAdapter.user_names",
            lambda _adapter: [{"user_id": 7, "friendly_name": "Viewer Name"}],
        )

        sync_tautulli(session, tautulli, {"api_key": "secret"}, policy)

        stored = session.scalar(select(Playback).where(Playback.external_row_id == "1"))
        assert stored is not None
        assert stored.user_name == "Viewer Name"
