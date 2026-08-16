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
    assert retention_deadline("MOVIE", imported, None) == imported + timedelta(weeks=16)
    assert retention_deadline("MOVIE", imported, watched) == watched + timedelta(weeks=8)
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
