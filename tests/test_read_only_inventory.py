from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.integrations.qbittorrent import QBittorrentAdapter
from app.persistence.models import (
    IntegrationInstance,
    InventoryPolicy,
    MediaFileRevision,
    MediaIdentity,
    MediaLifecycle,
    Playback,
    SourceFreshness,
)
from app.services.inventory import (
    _apply_playback,
    meaningful_playback,
    recompute_decisions,
    retention_deadline,
    sync_integration,
)
from app.services.scheduler import run_due_inventory_syncs


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


def client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return factory


def test_retention_and_meaningful_playback_rules() -> None:
    imported = datetime(2026, 1, 1, tzinfo=UTC)
    watched = datetime(2026, 2, 1, tzinfo=UTC)
    pre_import_watch = datetime(2025, 4, 29, tzinfo=UTC)
    assert retention_deadline("MOVIE", imported, None) == imported + timedelta(weeks=16)
    assert retention_deadline("MOVIE", imported, watched) == watched + timedelta(weeks=8)
    assert retention_deadline("SEASON", imported, pre_import_watch) == imported + timedelta(
        weeks=16
    )
    assert retention_deadline("SEASON", None, watched) is None
    assert meaningful_playback(599, 9.9, False) is False
    assert meaningful_playback(600, 0, False) is True
    assert meaningful_playback(30, 10, False) is True
    assert meaningful_playback(1, 1, True) is True


def test_disabled_integration_cannot_sync(app) -> None:
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="RADARR",
            name="Movies",
            base_url="http://radarr:7878",
            enabled=False,
            management_mode="PROTECTED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(integration)
        session.flush()
        with pytest.raises(ValueError, match="Enable"):
            sync_integration(session, integration, app.state.credential_cipher)


def test_scheduler_skips_disabled_integrations(app, monkeypatch) -> None:
    with app.state.database.session_factory() as session:
        session.add(
            IntegrationInstance(
                kind="RADARR",
                name="Disabled movies",
                base_url="http://radarr:7878",
                enabled=False,
                management_mode="MANAGED",
                credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
            )
        )
        session.commit()
    monkeypatch.setattr(
        "app.services.scheduler.sync_integration",
        lambda *_args: pytest.fail("disabled integration was scheduled"),
    )
    assert run_due_inventory_syncs(app.state.database, app.state.credential_cipher) == 0


def test_radarr_sync_normalizes_lifecycle_and_file(app, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.inventory.ArrAdapter.inventory",
        lambda _adapter, _kind: {
            "items": [
                {
                    "id": 42,
                    "title": "Arrival",
                    "year": 2016,
                    "tmdbId": 329865,
                    "hasFile": True,
                    "movieFile": {
                        "id": 99,
                        "movieId": 42,
                        "path": "/movies/Arrival/Arrival.mkv",
                        "size": 1234,
                        "dateAdded": "2026-01-10T12:00:00Z",
                        "quality": {"quality": {"name": "Bluray-1080p"}},
                    },
                }
            ],
            "files": [],
            "history": {
                "records": [
                    {
                        "movieId": 42,
                        "downloadId": "ABCDEF1234",
                        "eventType": "downloadFolderImported",
                    }
                ]
            },
        },
    )
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="RADARR",
            name="Movies",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="PROTECTED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(integration)
        session.flush()
        run = sync_integration(session, integration, app.state.credential_cipher)
        session.commit()
        assert run.status == "SUCCEEDED"

    with app.state.database.session_factory() as session:
        identity = session.scalar(select(MediaIdentity))
        lifecycle = session.scalar(select(MediaLifecycle))
        revision = session.scalar(select(MediaFileRevision))
        freshness = session.scalar(select(SourceFreshness))
        assert identity is not None and identity.tmdb_id == 329865
        assert lifecycle is not None
        assert lifecycle.first_imported_at == datetime(2026, 1, 10, 12)
        assert lifecycle.source_download_ids == ["abcdef1234"]
        assert lifecycle.decision == "KEEP_PROTECTED"
        assert revision is not None and revision.quality == "Bluray-1080p"
        assert freshness is not None and freshness.status == "FRESH"


def test_qbittorrent_api_key_inventory_is_get_only() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.headers["Authorization"] == "Bearer key"
        if request.url.path.endswith("/torrents/info"):
            return httpx.Response(200, json=[{"hash": "ABC", "name": "Movie"}])
        assert request.url.params["hash"] == "ABC"
        return httpx.Response(
            200,
            json=[{"url": "https://tracker.example/announce", "status": 2}],
        )

    rows = QBittorrentAdapter(
        "http://qbittorrent:8080", {"api_key": "key"}, client_factory(handler)
    ).inventory()
    assert rows[0]["trackers"][0]["status"] == 2
    assert methods == ["GET", "GET"]


def test_sonarr_sync_tracks_download_and_monitoring_state(app, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.inventory.ArrAdapter.inventory",
        lambda _adapter, _kind: {
            "items": [
                {
                    "id": 7,
                    "title": "The Show",
                    "tvdbId": 100,
                    "seasons": [
                        {"seasonNumber": 1, "monitored": True},
                        {"seasonNumber": 2, "monitored": False},
                    ],
                }
            ],
            "files": [
                {
                    "id": 11,
                    "seriesId": 7,
                    "seasonNumber": 1,
                    "size": 1234,
                    "dateAdded": "2026-01-10T12:00:00Z",
                }
            ],
            "episodes": [],
            "history": [],
            "tags": [],
        },
    )
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="SONARR",
            name="Sonarr",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(integration)
        session.flush()
        run = sync_integration(session, integration, app.state.credential_cipher)
        session.commit()
        assert run.status == "SUCCEEDED"

    with app.state.database.session_factory() as session:
        records = session.execute(
            select(MediaLifecycle, MediaIdentity)
            .join(MediaIdentity, MediaLifecycle.identity_id == MediaIdentity.id)
            .order_by(MediaIdentity.season_number)
        ).all()
        assert [(item.state, item.monitored) for item, _identity in records] == [
            ("ACTIVE", True),
            ("MISSING", False),
        ]
        assert records[1][0].decision == "NOT_IN_LIBRARY"
        assert records[1][0].decision_reason == "No downloaded files are present"


def test_tv_playback_resets_current_and_later_imported_seasons(app) -> None:
    watched_at = datetime(2026, 4, 2, 18, tzinfo=UTC)
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="SONARR",
            name="TV",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(integration)
        session.flush()
        lifecycles = []
        for season_number in (1, 2, 3):
            identity = MediaIdentity(
                media_type="SEASON",
                source_key=f"tvdb:100:season:{season_number}",
                canonical_title=f"Show · Season {season_number}",
                series_tvdb_id=100,
                season_number=season_number,
            )
            session.add(identity)
            session.flush()
            lifecycle = MediaLifecycle(
                identity_id=identity.id,
                integration_id=integration.id,
                arr_item_id=7,
                state="ACTIVE",
                plex_rating_key=f"season-{season_number}",
                first_imported_at=datetime(2026, 1, season_number, tzinfo=UTC),
            )
            session.add(lifecycle)
            lifecycles.append(lifecycle)
        session.add(
            Playback(
                integration_id=integration.id,
                external_row_id="watch-2",
                parent_rating_key="season-2",
                media_type="episode",
                watched_at=watched_at,
                duration_seconds=700,
                progress_percent=20,
                meaningful=True,
            )
        )
        _apply_playback(
            session,
            InventoryPolicy(
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
            ),
        )
        assert lifecycles[0].last_meaningful_watch_at is None
        assert lifecycles[1].last_meaningful_watch_at == watched_at.replace(tzinfo=None)
        assert lifecycles[2].last_meaningful_watch_at == watched_at.replace(tzinfo=None)


def test_tv_playback_does_not_predate_later_season_import(app) -> None:
    watched_at = datetime(2025, 4, 29, 18, tzinfo=UTC)
    imported_at = datetime(2025, 12, 3, 1, tzinfo=UTC)
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="SONARR",
            name="TV",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(integration)
        session.flush()
        lifecycles = []
        for season_number in (11, 12):
            identity = MediaIdentity(
                media_type="SEASON",
                source_key=f"tvdb:100:season:{season_number}",
                canonical_title=f"Vanderpump Rules · Season {season_number}",
                series_tvdb_id=100,
                season_number=season_number,
            )
            session.add(identity)
            session.flush()
            lifecycle = MediaLifecycle(
                identity_id=identity.id,
                integration_id=integration.id,
                arr_item_id=7,
                state="ACTIVE",
                plex_rating_key=f"season-{season_number}",
                first_imported_at=(
                    datetime(2024, 1, 1, tzinfo=UTC) if season_number == 11 else imported_at
                ),
            )
            session.add(lifecycle)
            lifecycles.append(lifecycle)
        session.add(
            Playback(
                integration_id=integration.id,
                external_row_id="watch-11",
                parent_rating_key="season-11",
                media_type="episode",
                watched_at=watched_at,
                duration_seconds=700,
                progress_percent=20,
                meaningful=True,
            )
        )
        _apply_playback(
            session,
            InventoryPolicy(
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
            ),
        )

        assert lifecycles[1].last_meaningful_watch_at is None
        assert lifecycles[1].retention_deadline == imported_at + timedelta(weeks=16)


def test_stale_decision_names_the_blocking_integration(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Sonarr-1080P",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        plex = IntegrationInstance(
            kind="PLEX",
            name="Plex Media Server",
            base_url="http://plex:32400",
            enabled=True,
            management_mode="PROTECTED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        identity = MediaIdentity(
            media_type="SEASON",
            source_key="tvdb:100:season:1",
            canonical_title="The Show · Season 1",
            series_tvdb_id=100,
            season_number=1,
        )
        session.add_all([sonarr, plex, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=sonarr.id,
            arr_item_id=7,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            protection_sources=[],
            first_imported_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        session.add(lifecycle)
        session.add(
            SourceFreshness(
                integration_id=sonarr.id,
                source_kind="SONARR",
                status="FRESH",
                last_success_at=datetime.now(UTC),
                stale_after_seconds=3600,
            )
        )

        recompute_decisions(session)

        assert lifecycle.decision == "BLOCKED_STALE"
        assert lifecycle.decision_reason == "Waiting for fresh data from Plex Media Server"
        session.commit()
        lifecycle_id = lifecycle.id

    workbench = client.get("/media")
    detail = client.get(f"/media/{lifecycle_id}")
    assert "Blocked · source data stale" in workbench.text
    assert "Waiting for fresh data from Plex Media Server" in workbench.text
    assert "Blocked · source data stale" in detail.text
    assert "Waiting for fresh data from Plex Media Server" in detail.text


def test_recompute_clears_a_watch_that_predates_import(app) -> None:
    imported_at = datetime(2025, 12, 3, 1, tzinfo=UTC)
    with app.state.database.session_factory() as session:
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Sonarr",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        identity = MediaIdentity(
            media_type="SEASON",
            source_key="tvdb:100:season:12",
            canonical_title="Vanderpump Rules · Season 12",
            series_tvdb_id=100,
            season_number=12,
        )
        session.add_all([sonarr, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=sonarr.id,
            arr_item_id=7,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            protection_sources=[],
            first_imported_at=imported_at,
            last_meaningful_watch_at=datetime(2025, 4, 29, 18, tzinfo=UTC),
            watched=True,
        )
        session.add(lifecycle)
        session.add(
            SourceFreshness(
                integration_id=sonarr.id,
                source_kind="SONARR",
                status="FRESH",
                last_success_at=datetime.now(UTC),
                stale_after_seconds=3600,
            )
        )

        recompute_decisions(session)

        assert lifecycle.last_meaningful_watch_at is None
        assert lifecycle.watched is False
        assert lifecycle.retention_deadline == imported_at + timedelta(weeks=16)


def test_failed_sync_immediately_blocks_existing_decisions(app, monkeypatch) -> None:
    def fail_requests(_adapter):
        raise httpx.ConnectError("service unavailable")

    monkeypatch.setattr("app.services.inventory.OverseerrAdapter.requests", fail_requests)
    with app.state.database.session_factory() as session:
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Sonarr",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        overseerr = IntegrationInstance(
            kind="OVERSEERR",
            name="MediaMule Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            management_mode="PROTECTED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        identity = MediaIdentity(
            media_type="SEASON",
            source_key="tvdb:100:season:1",
            canonical_title="The Show · Season 1",
            series_tvdb_id=100,
            season_number=1,
        )
        session.add_all([sonarr, overseerr, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=sonarr.id,
            arr_item_id=7,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            protection_sources=[],
            first_imported_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
        session.add(lifecycle)
        now = datetime.now(UTC)
        session.add_all(
            [
                SourceFreshness(
                    integration_id=integration.id,
                    source_kind=integration.kind,
                    status="FRESH",
                    last_success_at=now,
                    stale_after_seconds=3600,
                )
                for integration in (sonarr, overseerr)
            ]
        )
        recompute_decisions(session)
        assert lifecycle.decision == "REVIEW_ELIGIBLE"

        run = sync_integration(session, overseerr, app.state.credential_cipher)

        assert run.status == "FAILED"
        assert lifecycle.decision == "BLOCKED_STALE"
        assert lifecycle.decision_reason == "Waiting for fresh data from MediaMule Requests"


def test_media_workbench_and_policy_are_web_configurable(client, app) -> None:
    authenticate(client)
    page = client.get("/media")
    assert page.status_code == 200
    assert "Decision workbench" in page.text
    response = client.post(
        "/media/policy",
        data={
            "csrf": csrf_from(page),
            "meaningful_minutes": 12,
            "meaningful_percent": 15,
            "never_watched_weeks": 18,
            "watched_weeks": 9,
            "protected_tag_name": "keep-forever",
            "tautulli_fresh_minutes": 10,
            "torrent_fresh_minutes": 11,
            "arr_fresh_minutes": 50,
            "overseerr_fresh_minutes": 55,
            "plex_fresh_minutes": 45,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Inventory policy saved" in response.text
    assert 'name="meaningful_minutes" min="1" max="1440" value="12"' in response.text


def test_media_workbench_groups_tv_and_filters_large_inventory(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Sonarr",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr-4K",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="PROTECTED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add_all([sonarr, radarr])
        session.flush()
        for number, state, monitored in ((1, "ACTIVE", True), (2, "MISSING", False)):
            identity = MediaIdentity(
                media_type="SEASON",
                source_key=f"tvdb:100:season:{number}",
                canonical_title=f"The Show · Season {number}",
                series_tvdb_id=100,
                season_number=number,
            )
            session.add(identity)
            session.flush()
            session.add(
                MediaLifecycle(
                    identity_id=identity.id,
                    integration_id=sonarr.id,
                    arr_item_id=7,
                    state=state,
                    monitored=monitored,
                    protection_state="UNPROTECTED",
                    decision="REVIEW_ELIGIBLE" if state == "MISSING" else "BLOCKED_UNKNOWN",
                    decision_reason="Source data incomplete",
                )
            )
        movie_identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:200",
            canonical_title="The Movie",
            tmdb_id=200,
        )
        session.add(movie_identity)
        session.flush()
        session.add(
            MediaLifecycle(
                identity_id=movie_identity.id,
                integration_id=radarr.id,
                arr_item_id=8,
                state="ACTIVE",
                monitored=True,
                protection_state="PROTECTED",
                decision="KEEP_PROTECTED",
                decision_reason="Protected by INSTANCE_MODE",
            )
        )
        session.commit()
        sonarr_id = sonarr.id
        radarr_id = radarr.id

    page = client.get("/media")

    assert "The Show" in page.text
    assert "2 seasons" in page.text
    assert "Not downloaded" in page.text
    assert "Unmonitored in Sonarr" in page.text
    assert 'name="source"' in page.text
    assert 'name="media_type"' in page.text
    assert 'name="library_state"' in page.text
    assert 'name="decision"' in page.text
    assert 'name="watch_state"' in page.text
    assert 'name="sort"' in page.text

    radarr_only = client.get(f"/media?source={radarr_id}")
    assert "The Movie" in radarr_only.text
    assert "The Show" not in radarr_only.text

    missing_tv = client.get(f"/media?source={sonarr_id}&library_state=MISSING")
    assert "The Show" in missing_tv.text
    assert "Season 2" in missing_tv.text
    assert "Season 1" not in missing_tv.text
    assert "The Movie" not in missing_tv.text

    review_eligible = client.get("/media?decision=REVIEW_ELIGIBLE")
    assert "The Show" not in review_eligible.text
