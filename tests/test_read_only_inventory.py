from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.integrations.qbittorrent import QBittorrentAdapter
from app.persistence.models import (
    EventRecord,
    IntegrationInstance,
    IntegrationLibraryMapping,
    InventoryPolicy,
    ManagedLibrary,
    MediaFileRevision,
    MediaIdentity,
    MediaLifecycle,
    Playback,
    RequesterProfile,
    RequestRecord,
    SourceFreshness,
    Torrent,
    TorrentMediaMapping,
)
from app.services.inventory import (
    _apply_playback,
    meaningful_playback,
    recompute_decisions,
    retention_deadline,
    set_manual_protection,
    sync_integration,
    sync_overseerr,
    sync_plex,
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


def test_plex_sync_does_not_collapse_duplicate_arr_lifecycles(app) -> None:
    with app.state.database.session_factory() as session:
        plex = IntegrationInstance(
            kind="PLEX",
            name="Plex",
            base_url="http://plex:32400",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "plex"}),
        )
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "radarr"}),
        )
        radarr_4k = IntegrationInstance(
            kind="RADARR",
            name="Radarr 4K",
            base_url="http://radarr-4k:7878",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "radarr-4k"}),
        )
        policy = InventoryPolicy(id="default")
        session.add_all([plex, radarr, radarr_4k, policy])
        session.flush()
        movies = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="1",
            name="Movies",
            media_type="movie",
            enabled=True,
        )
        movies_4k = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="2",
            name="Movies 4K",
            media_type="movie",
            enabled=True,
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:123",
            tmdb_id=123,
            canonical_title="Shared Movie",
        )
        session.add_all([movies, movies_4k, identity])
        session.flush()
        normal = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr.id,
            arr_item_id=10,
            state="ACTIVE",
            current_path="/data/movies/Shared.Movie.1080p.mkv",
        )
        four_k = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr_4k.id,
            arr_item_id=20,
            state="ACTIVE",
            current_path="/data-4k/movies/Shared.Movie.2160p.mkv",
        )
        session.add_all([normal, four_k])
        session.flush()

        sync_plex(
            session,
            plex,
            {"api_key": "plex"},
            policy,
            library_payloads=[
                (
                    movies,
                    [
                        {
                            "ratingKey": "normal-key",
                            "Guid": [{"id": "tmdb://123"}],
                            "Media": [{"Part": [{"file": "/plex/movies/Shared.Movie.1080p.mkv"}]}],
                        }
                    ],
                ),
                (
                    movies_4k,
                    [
                        {
                            "ratingKey": "4k-key",
                            "Guid": [{"id": "tmdb://123"}],
                            "Media": [
                                {"Part": [{"file": "/plex/movies-4k/Shared.Movie.2160p.mkv"}]}
                            ],
                        }
                    ],
                ),
            ],
        )

        assert {normal.plex_rating_key, four_k.plex_rating_key} == {"normal-key", "4k-key"}
        assert {normal.library_id, four_k.library_id} == {movies.id, movies_4k.id}
        mappings = session.scalars(select(IntegrationLibraryMapping)).all()
        assert {
            (mapping.integration_id, mapping.library_id, mapping.source)
            for mapping in mappings
        } == {
            (radarr.id, movies.id, "AUTO"),
            (radarr_4k.id, movies_4k.id, "AUTO"),
        }


def test_plex_sync_requires_filename_evidence_before_automatic_pairing(app) -> None:
    with app.state.database.session_factory() as session:
        plex = IntegrationInstance(
            kind="PLEX",
            name="Plex",
            base_url="http://plex:32400",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "plex"}),
        )
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "radarr"}),
        )
        policy = InventoryPolicy(id="default")
        session.add_all([plex, radarr, policy])
        session.flush()
        movies = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="1",
            name="Movies",
            media_type="movie",
            enabled=True,
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:123",
            tmdb_id=123,
            canonical_title="Shared Movie",
        )
        session.add_all([movies, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr.id,
            arr_item_id=10,
            state="ACTIVE",
            current_path="/data/movies/Expected.Release.mkv",
        )
        session.add(lifecycle)
        session.flush()

        sync_plex(
            session,
            plex,
            {"api_key": "plex"},
            policy,
            library_payloads=[
                (
                    movies,
                    [
                        {
                            "ratingKey": "wrong-key",
                            "Guid": [{"id": "tmdb://123"}],
                            "Media": [{"Part": [{"file": "/plex/Other.Release.mkv"}]}],
                        }
                    ],
                )
            ],
        )

        assert session.get(IntegrationLibraryMapping, radarr.id) is None
        assert lifecycle.library_id is None
        assert lifecycle.plex_rating_key is None


def test_automatic_pairing_can_reassign_a_library_without_unique_constraint_failure(
    app,
) -> None:
    with app.state.database.session_factory() as session:
        plex = IntegrationInstance(
            kind="PLEX",
            name="Plex",
            base_url="http://plex:32400",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "plex"}),
        )
        radarr_a = IntegrationInstance(
            kind="RADARR",
            name="Radarr A",
            base_url="http://radarr-a:7878",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "a"}),
        )
        radarr_b = IntegrationInstance(
            kind="RADARR",
            name="Radarr B",
            base_url="http://radarr-b:7878",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "b"}),
        )
        policy = InventoryPolicy(id="default")
        session.add_all([plex, radarr_a, radarr_b, policy])
        session.flush()
        movies = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="1",
            name="Movies",
            media_type="movie",
            enabled=True,
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:123",
            tmdb_id=123,
            canonical_title="Shared Movie",
        )
        session.add_all([movies, identity])
        session.flush()
        session.add_all(
            [
                IntegrationLibraryMapping(
                    integration_id=radarr_a.id,
                    library_id=movies.id,
                    source="AUTO",
                ),
                MediaLifecycle(
                    identity_id=identity.id,
                    integration_id=radarr_a.id,
                    arr_item_id=10,
                    state="ACTIVE",
                    current_path="/data/A.Release.mkv",
                ),
                MediaLifecycle(
                    identity_id=identity.id,
                    integration_id=radarr_b.id,
                    arr_item_id=20,
                    state="ACTIVE",
                    current_path="/data/B.Release.mkv",
                ),
            ]
        )
        session.flush()

        sync_plex(
            session,
            plex,
            {"api_key": "plex"},
            policy,
            library_payloads=[
                (
                    movies,
                    [
                        {
                            "ratingKey": "b-key",
                            "Guid": [{"id": "tmdb://123"}],
                            "Media": [{"Part": [{"file": "/plex/B.Release.mkv"}]}],
                        }
                    ],
                )
            ],
        )

        assert session.get(IntegrationLibraryMapping, radarr_a.id) is None
        mapping = session.get(IntegrationLibraryMapping, radarr_b.id)
        assert mapping is not None
        assert mapping.library_id == movies.id
        assert mapping.source == "AUTO"


def test_empty_plex_sync_preserves_validated_auto_pairing_after_deletion(app) -> None:
    with app.state.database.session_factory() as session:
        plex = IntegrationInstance(
            kind="PLEX",
            name="Plex",
            base_url="http://plex:32400",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "plex"}),
        )
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "radarr"}),
        )
        policy = InventoryPolicy(id="default")
        session.add_all([plex, radarr, policy])
        session.flush()
        movies = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="1",
            name="Movies",
            media_type="movie",
            enabled=True,
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:123",
            tmdb_id=123,
            canonical_title="Deleted Movie",
        )
        session.add_all([movies, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr.id,
            arr_item_id=10,
            state="DELETED",
            current_path="/data/Deleted.Movie.mkv",
        )
        session.add_all(
            [
                lifecycle,
                IntegrationLibraryMapping(
                    integration_id=radarr.id,
                    library_id=movies.id,
                    source="AUTO",
                ),
            ]
        )
        session.flush()

        sync_plex(
            session,
            plex,
            {"api_key": "plex"},
            policy,
            library_payloads=[(movies, [])],
        )

        mapping = session.get(IntegrationLibraryMapping, radarr.id)
        assert mapping is not None
        assert mapping.library_id == movies.id
        assert mapping.source == "AUTO"
        assert lifecycle.library_id == movies.id
        assert lifecycle.plex_rating_key is None


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
        assert run.status == "SUCCEEDED", run.sanitized_error

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
        set_manual_protection(session, [records[0][0]], protected=True)
        session.commit()
        integration_id = records[0][0].integration_id

    with app.state.database.session_factory() as session:
        integration = session.get(IntegrationInstance, integration_id)
        assert integration is not None
        run = sync_integration(session, integration, app.state.credential_cipher)
        session.commit()
        assert run.status == "SUCCEEDED", run.sanitized_error
        protected = session.scalar(
            select(MediaLifecycle).where(MediaLifecycle.state == "ACTIVE")
        )
        assert protected is not None
        assert "MANUAL_SELECTION" in protected.protection_sources
        assert protected.protection_state == "PROTECTED"


def test_sonarr_sync_names_specials_and_clears_missing_retention(app, monkeypatch) -> None:
    payload = {
        "items": [
            {
                "id": 7,
                "title": "The Show",
                "tvdbId": 100,
                "seasons": [{"seasonNumber": 0, "monitored": True}],
            }
        ],
        "files": [
            {
                "id": 11,
                "seriesId": 7,
                "seasonNumber": 0,
                "size": 1234,
                "dateAdded": "2020-01-10T12:00:00Z",
            }
        ],
        "episodes": [],
        "history": [],
        "tags": [],
    }
    monkeypatch.setattr(
        "app.services.inventory.ArrAdapter.inventory",
        lambda _adapter, _kind: payload,
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
        first_run = sync_integration(session, integration, app.state.credential_cipher)
        session.commit()
        assert first_run.status == "SUCCEEDED"
        integration_id = integration.id

    payload["files"] = []
    with app.state.database.session_factory() as session:
        integration = session.get(IntegrationInstance, integration_id)
        assert integration is not None
        second_run = sync_integration(session, integration, app.state.credential_cipher)
        session.commit()
        assert second_run.status == "SUCCEEDED"

    with app.state.database.session_factory() as session:
        identity = session.scalar(select(MediaIdentity))
        lifecycle = session.scalar(select(MediaLifecycle))
        assert identity is not None and identity.canonical_title == "The Show · Specials"
        assert lifecycle is not None and lifecycle.state == "MISSING"
        assert lifecycle.retention_deadline is None


def test_removing_manual_protection_preserves_other_protection_sources(app) -> None:
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="RADARR",
            name="Shared Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:909",
            canonical_title="Requested Movie",
            tmdb_id=909,
        )
        session.add_all([integration, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=integration.id,
            arr_item_id=909,
            state="ACTIVE",
            protection_state="PROTECTED",
            protection_sources=["PROTECTED_REQUESTER", "MANUAL_SELECTION"],
        )
        session.add(lifecycle)

        changed = set_manual_protection(session, [lifecycle], protected=False)

        assert changed == [lifecycle]
        assert lifecycle.protection_sources == ["PROTECTED_REQUESTER"]
        assert lifecycle.protection_state == "PROTECTED"
        assert lifecycle.decision == "KEEP_PROTECTED"


def test_overseerr_sync_stores_requester_identity_unprotected_by_default(app) -> None:
    with app.state.database.session_factory() as session:
        overseerr = IntegrationInstance(
            kind="OVERSEERR",
            name="MediaMule Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:200",
            canonical_title="The Movie",
            tmdb_id=200,
        )
        session.add_all([overseerr, radarr, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr.id,
            arr_item_id=8,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            protection_sources=[],
        )
        session.add(lifecycle)

        sync_overseerr(
            session,
            overseerr,
            {"api_key": "secret"},
            rows=[
                {
                    "id": 44,
                    "type": "movie",
                    "status": 2,
                    "media": {"tmdbId": 200},
                    "requestedBy": {
                        "id": 9,
                        "username": "viewer",
                        "displayName": "Viewer Name",
                        "email": "viewer@example.com",
                    },
                    "createdAt": "2026-01-01T12:00:00Z",
                },
                {
                    "id": 45,
                    "type": "movie",
                    "status": 2,
                    "media": {"tmdbId": 200},
                    "requestedBy": {
                        "id": 9,
                        "username": "viewer",
                        "displayName": "Viewer Name",
                        "email": "viewer@example.com",
                    },
                    "createdAt": "2026-01-02T12:00:00Z",
                },
            ],
        )

        profile = session.scalar(select(RequesterProfile))
        request_records = session.scalars(select(RequestRecord)).all()
        assert profile is not None
        assert profile.external_id == "9"
        assert profile.username == "viewer"
        assert profile.display_name == "Viewer Name"
        assert profile.email == "viewer@example.com"
        assert profile.protected is False
        assert {record.external_request_id for record in request_records} == {44, 45}
        assert {record.requester_id for record in request_records} == {"9"}
        assert lifecycle.protection_state == "UNPROTECTED"
        assert "PROTECTED_REQUESTER" not in lifecycle.protection_sources

        profile.protected = True
        sync_overseerr(
            session,
            overseerr,
            {"api_key": "secret"},
            rows=[
                {
                    "id": 44,
                    "type": "movie",
                    "status": 2,
                    "media": {"tmdbId": 200},
                    "requestedBy": {"id": 9},
                }
            ],
        )

        assert profile.protected is True
        assert profile.username == "viewer"
        assert profile.display_name == "Viewer Name"
        assert profile.email == "viewer@example.com"
        assert lifecycle.protection_state == "PROTECTED"
        assert "PROTECTED_REQUESTER" in lifecycle.protection_sources


def test_requester_protection_is_web_configurable_and_visible(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        overseerr = IntegrationInstance(
            kind="OVERSEERR",
            name="MediaMule Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        profile = RequesterProfile(
            integration_id="pending",
            external_id="9",
            username="viewer",
            display_name="Viewer Name",
            email="viewer@example.com",
            protected=False,
        )
        session.add(overseerr)
        session.flush()
        profile.integration_id = overseerr.id
        session.add(profile)
        session.commit()
        profile_id = profile.id

    page = client.get("/integrations")
    assert "Viewer Name" in page.text
    assert "@viewer" in page.text
    assert "viewer@example.com" in page.text
    assert "Not protected" in page.text

    response = client.post(
        f"/requesters/{profile_id}/protected",
        data={"csrf": csrf_from(page)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Requester protection enabled for Viewer Name" in response.text
    with app.state.database.session_factory() as session:
        profile = session.get(RequesterProfile, profile_id)
        assert profile is not None and profile.protected is True


def test_running_sync_disables_all_sync_actions_and_returns_a_friendly_conflict(
    client, app
) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="OVERSEERR",
            name="MediaMule Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(integration)
        session.commit()
        integration_id = integration.id

    with app.state.sync_coordinator.acquire(
        "sonarr-id", "Sonarr-1080P", trigger="scheduled"
    ):
        page = client.get("/integrations")
        assert "Sync running: Sonarr-1080P" in page.text
        assert re.search(
            rf'action="/integrations/{integration_id}/sync".*?<button[^>]+disabled',
            page.text,
            re.DOTALL,
        )

        response = client.post(
            f"/integrations/{integration_id}/sync",
            data={"csrf": csrf_from(page)},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert "Sync running: Sonarr-1080P. Try again after it finishes." in response.text


def test_scheduler_skips_when_another_inventory_sync_is_running(app, monkeypatch) -> None:
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="OVERSEERR",
            name="MediaMule Requests",
            base_url="http://overseerr:5055",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(integration)
        session.commit()

    monkeypatch.setattr(
        "app.services.scheduler.sync_integration",
        lambda *_args: pytest.fail("Scheduler started a second inventory writer"),
    )
    with app.state.sync_coordinator.acquire(
        "sonarr-id", "Sonarr-1080P", trigger="manual"
    ):
        completed = run_due_inventory_syncs(
            app.state.database,
            app.state.credential_cipher,
            app.state.sync_coordinator,
        )

    assert completed == 0


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
        assert lifecycles[2].last_meaningful_watch_at is None
        assert lifecycles[2].retention_deadline == watched_at.replace(
            tzinfo=None
        ) + timedelta(weeks=8)


def test_tv_forward_reset_preserves_each_seasons_actual_watch_date(app) -> None:
    season_11_watch = datetime(2026, 6, 29, 12, tzinfo=UTC)
    season_12_watch = datetime(2025, 9, 16, 12, tzinfo=UTC)
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
        for season_number, imported_at in (
            (11, datetime(2024, 2, 6, 12, tzinfo=UTC)),
            (12, datetime(2025, 6, 3, 12, tzinfo=UTC)),
        ):
            identity = MediaIdentity(
                media_type="SEASON",
                source_key=f"tvdb:100:season:{season_number}",
                canonical_title=f"Below Deck · Season {season_number}",
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
                first_imported_at=imported_at,
            )
            session.add(lifecycle)
            lifecycles.append(lifecycle)
        session.add_all(
            [
                Playback(
                    integration_id=integration.id,
                    external_row_id="watch-11",
                    parent_rating_key="season-11",
                    media_type="episode",
                    watched_at=season_11_watch,
                    duration_seconds=700,
                    progress_percent=20,
                    meaningful=True,
                ),
                Playback(
                    integration_id=integration.id,
                    external_row_id="watch-12",
                    parent_rating_key="season-12",
                    media_type="episode",
                    watched_at=season_12_watch,
                    duration_seconds=700,
                    progress_percent=20,
                    meaningful=True,
                ),
            ]
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

        assert lifecycles[0].last_meaningful_watch_at == season_11_watch.replace(tzinfo=None)
        assert lifecycles[1].last_meaningful_watch_at == season_12_watch.replace(tzinfo=None)
        assert lifecycles[1].retention_deadline == season_11_watch.replace(
            tzinfo=None
        ) + timedelta(weeks=8)

        recompute_decisions(session)
        assert lifecycles[0].last_meaningful_watch_at == season_11_watch.replace(tzinfo=None)
        assert lifecycles[1].last_meaningful_watch_at == season_12_watch.replace(tzinfo=None)
        assert lifecycles[1].retention_deadline == season_11_watch.replace(
            tzinfo=None
        ) + timedelta(weeks=8)


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


def test_media_detail_separates_current_files_from_previous_revisions(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr-1080P",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:51052",
            canonical_title="Arthur Christmas",
            tmdb_id=51052,
            year=2011,
        )
        session.add_all([radarr, identity])
        session.flush()
        lifecycle = MediaLifecycle(
            identity_id=identity.id,
            integration_id=radarr.id,
            arr_item_id=42,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            protection_sources=[],
            current_path="/movies/Arthur Christmas/current-remux.mkv",
            first_imported_at=datetime(2022, 11, 24, tzinfo=UTC),
        )
        session.add(lifecycle)
        session.flush()
        session.add_all(
            [
                MediaFileRevision(
                    lifecycle_id=lifecycle.id,
                    arr_file_id=1,
                    path="/movies/Arthur Christmas/previous-release.mp4",
                    imported_at=datetime(2022, 11, 24, tzinfo=UTC),
                    quality="Bluray-1080p",
                    active=False,
                ),
                MediaFileRevision(
                    lifecycle_id=lifecycle.id,
                    arr_file_id=2,
                    path="/movies/Arthur Christmas/current-remux.mkv",
                    imported_at=datetime(2026, 8, 18, tzinfo=UTC),
                    quality="Remux-1080p",
                    active=True,
                ),
            ]
        )
        session.commit()
        lifecycle_id = lifecycle.id

    detail = client.get(f"/media/{lifecycle_id}")

    assert detail.status_code == 200
    assert "Current file" in detail.text
    assert "Current in Radarr-1080P" in detail.text
    assert "1 previous revision" in detail.text
    assert "Previously observed; not currently reported by Radarr-1080P" in detail.text
    assert detail.text.index("current-remux.mkv") < detail.text.index("previous-release.mp4")
    assert '<details class="revision-history">' in detail.text
    assert "Files and revisions" not in detail.text


def test_media_detail_names_every_other_title_sharing_a_torrent(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Radarr-1080P",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Sonarr-1080P",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        qbit = IntegrationInstance(
            kind="QBITTORRENT",
            name="qBittorrent",
            base_url="http://qbittorrent:8080",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        arthur = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:51052",
            canonical_title="Arthur Christmas",
        )
        shared_season = MediaIdentity(
            media_type="SEASON",
            source_key="tvdb:123:season:1",
            canonical_title="Holiday Collection · Season 1",
            season_number=1,
        )
        session.add_all([radarr, sonarr, qbit, arthur, shared_season])
        session.flush()
        arthur_lifecycle = MediaLifecycle(
            identity_id=arthur.id,
            integration_id=radarr.id,
            arr_item_id=42,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            current_path="/movies/Arthur Christmas/current-remux.mkv",
        )
        season_lifecycle = MediaLifecycle(
            identity_id=shared_season.id,
            integration_id=sonarr.id,
            arr_item_id=7,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            current_path="/tv/Holiday Collection/Season 01/episode.mkv",
        )
        torrent = Torrent(
            integration_id=qbit.id,
            info_hash="shared-hash",
            name="Shared holiday torrent",
            present=True,
        )
        session.add_all([arthur_lifecycle, season_lifecycle, torrent])
        session.flush()
        session.add_all(
            [
                TorrentMediaMapping(
                    torrent_id=torrent.id,
                    lifecycle_id=arthur_lifecycle.id,
                    mapping_source="ARR_DOWNLOAD_ID",
                    confidence="EXACT",
                ),
                TorrentMediaMapping(
                    torrent_id=torrent.id,
                    lifecycle_id=season_lifecycle.id,
                    mapping_source="CONTENT_PATH",
                    confidence="HIGH",
                ),
            ]
        )
        session.commit()
        lifecycle_id = arthur_lifecycle.id
        shared_lifecycle_id = season_lifecycle.id

    detail = client.get(f"/media/{lifecycle_id}")

    assert detail.status_code == 200
    assert "1 torrent linked to this title" in detail.text
    assert "Also linked to 1 other title" in detail.text
    assert "Holiday Collection · Season 1" in detail.text
    assert "Sonarr-1080P · High confidence content path match" in detail.text
    assert f'href="/media/{shared_lifecycle_id}"' in detail.text


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


def test_settings_owns_policy_configuration_and_media_stays_focused(client, app) -> None:
    authenticate(client)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Retention and freshness" in page.text
    assert 'action="/settings/policy"' in page.text
    media_page = client.get("/media")
    assert media_page.status_code == 200
    assert "Decision workbench" in media_page.text
    assert 'name="meaningful_minutes"' not in media_page.text
    response = client.post(
        "/settings/policy",
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
    assert "Retention settings saved" in response.text
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
                    protection_state="PROTECTED" if number == 1 else "UNPROTECTED",
                    protection_sources=["MANUAL_SELECTION"] if number == 1 else [],
                    decision="REVIEW_ELIGIBLE" if state == "MISSING" else "KEEP_PROTECTED",
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
    assert "1 of 2 seasons protected" in page.text
    assert '<span class="series-protection-state">' in page.text
    assert '<svg aria-hidden="true" viewBox="0 0 24 24">' not in page.text
    assert "/static/app.css?v=" in page.text
    assert "/static/media-selection.js?v=" in page.text
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

    never_watched = client.get("/media?watch_state=NEVER_WATCHED")
    assert "Season 1" in never_watched.text
    assert "Season 2" not in never_watched.text


def test_series_summary_names_specials_and_ignores_missing_season_deadlines(client, app) -> None:
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
        session.add(sonarr)
        session.flush()
        identities = [
            MediaIdentity(
                media_type="SEASON",
                source_key=f"tvdb:100:season:{season}",
                canonical_title=f"Below Deck Sailing Yacht · Season {season}",
                series_tvdb_id=100,
                season_number=season,
                year=2020,
            )
            for season in (0, 5)
        ]
        session.add_all(identities)
        session.flush()
        session.add_all(
            [
                MediaLifecycle(
                    identity_id=identities[0].id,
                    integration_id=sonarr.id,
                    arr_item_id=10,
                    state="MISSING",
                    protection_state="UNPROTECTED",
                    protection_sources=[],
                    retention_deadline=datetime(2020, 9, 8, 12, tzinfo=UTC),
                ),
                MediaLifecycle(
                    identity_id=identities[1].id,
                    integration_id=sonarr.id,
                    arr_item_id=10,
                    state="ACTIVE",
                    protection_state="UNPROTECTED",
                    protection_sources=[],
                    first_imported_at=datetime(2024, 10, 8, 12, tzinfo=UTC),
                    retention_deadline=datetime(2025, 3, 26, 12, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

    page = client.get("/media?search=Below+Deck+Sailing+Yacht")

    assert page.status_code == 200
    assert "Below Deck Sailing Yacht · Specials" in page.text
    assert "Season 0" not in page.text
    assert "1 season + specials" in page.text
    assert "2025-03-26" in page.text
    assert "2020-09-08" not in page.text


def test_media_manual_protection_is_selective_reversible_and_visible(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Shared Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add(radarr)
        session.flush()
        lifecycle_ids = []
        for tmdb_id, title in ((201, "Keep This"), (202, "Review This")):
            identity = MediaIdentity(
                media_type="MOVIE",
                source_key=f"tmdb:{tmdb_id}",
                canonical_title=title,
                tmdb_id=tmdb_id,
            )
            session.add(identity)
            session.flush()
            lifecycle = MediaLifecycle(
                identity_id=identity.id,
                integration_id=radarr.id,
                arr_item_id=tmdb_id,
                state="ACTIVE",
                protection_state="UNPROTECTED",
                protection_sources=[],
                decision="REVIEW_ELIGIBLE",
                decision_reason="Retention elapsed",
            )
            session.add(lifecycle)
            session.flush()
            lifecycle_ids.append(lifecycle.id)
        session.commit()

    page = client.get("/media")
    assert "Select all 2 filtered lifecycles" in page.text
    assert f'value="{lifecycle_ids[0]}"' in page.text

    empty_selection = client.post(
        "/media/protection",
        data={"csrf": csrf_from(page), "operation": "protect"},
        follow_redirects=True,
    )
    assert "Select at least one lifecycle or choose all filtered results." in empty_selection.text

    protected = client.post(
        "/media/protection",
        data={
            "csrf": csrf_from(page),
            "operation": "protect",
            "lifecycle_ids": [lifecycle_ids[0]],
        },
        follow_redirects=True,
    )
    assert protected.status_code == 200
    assert "Manual protection applied to 1 lifecycle" in protected.text
    with app.state.database.session_factory() as session:
        first = session.get(MediaLifecycle, lifecycle_ids[0])
        second = session.get(MediaLifecycle, lifecycle_ids[1])
        assert first is not None and first.protection_sources == ["MANUAL_SELECTION"]
        assert first.protection_state == "PROTECTED"
        assert first.decision == "KEEP_PROTECTED"
        assert second is not None and second.protection_sources == []

    removed = client.post(
        "/media/protection",
        data={
            "csrf": csrf_from(protected),
            "operation": "unprotect",
            "lifecycle_ids": [lifecycle_ids[0]],
        },
        follow_redirects=True,
    )
    assert "Manual protection removed from 1 lifecycle" in removed.text
    with app.state.database.session_factory() as session:
        first = session.get(MediaLifecycle, lifecycle_ids[0])
        assert first is not None and "MANUAL_SELECTION" not in first.protection_sources
        assert first.protection_state == "UNPROTECTED"


def test_media_manual_protection_can_apply_to_every_filtered_result(client, app) -> None:
    authenticate(client)
    with app.state.database.session_factory() as session:
        sonarr = IntegrationInstance(
            kind="SONARR",
            name="Shared Sonarr",
            base_url="http://sonarr:8989",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        radarr = IntegrationInstance(
            kind="RADARR",
            name="Shared Radarr",
            base_url="http://radarr:7878",
            enabled=True,
            management_mode="MANAGED",
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add_all([sonarr, radarr])
        session.flush()
        active_sonarr_ids = []
        for index in range(51):
            identity = MediaIdentity(
                media_type="SEASON",
                source_key=f"sonarr:{1000 + index}:season:1",
                canonical_title=f"Filtered Show {index:02d} · Season 1",
                series_tvdb_id=1000 + index,
                season_number=1,
            )
            session.add(identity)
            session.flush()
            lifecycle = MediaLifecycle(
                identity_id=identity.id,
                integration_id=sonarr.id,
                arr_item_id=1000 + index,
                state="ACTIVE",
                protection_state="UNPROTECTED",
                protection_sources=[],
                decision="REVIEW_ELIGIBLE",
                decision_reason="Retention elapsed",
            )
            session.add(lifecycle)
            session.flush()
            active_sonarr_ids.append(lifecycle.id)
        missing_identity = MediaIdentity(
            media_type="SEASON",
            source_key="sonarr:2000:season:1",
            canonical_title="Missing Show · Season 1",
            series_tvdb_id=2000,
            season_number=1,
        )
        movie_identity = MediaIdentity(
            media_type="MOVIE",
            source_key="tmdb:3000",
            canonical_title="Other Movie",
            tmdb_id=3000,
        )
        session.add_all([missing_identity, movie_identity])
        session.flush()
        missing = MediaLifecycle(
            identity_id=missing_identity.id,
            integration_id=sonarr.id,
            arr_item_id=2000,
            state="MISSING",
            protection_state="UNPROTECTED",
            protection_sources=[],
            decision="NOT_IN_LIBRARY",
            decision_reason="No downloaded files are present",
        )
        other_source = MediaLifecycle(
            identity_id=movie_identity.id,
            integration_id=radarr.id,
            arr_item_id=3000,
            state="ACTIVE",
            protection_state="UNPROTECTED",
            protection_sources=[],
            decision="REVIEW_ELIGIBLE",
            decision_reason="Retention elapsed",
        )
        session.add_all([missing, other_source])
        session.flush()
        missing_id = missing.id
        other_source_id = other_source.id
        session.commit()
        sonarr_id = sonarr.id

    page = client.get(f"/media?source={sonarr_id}&library_state=ACTIVE")
    assert "Select all 51 filtered lifecycles" in page.text
    assert "Page 1 of 2" in page.text
    response = client.post(
        "/media/protection",
        data={
            "csrf": csrf_from(page),
            "operation": "protect",
            "select_all_filtered": "yes",
            "source": sonarr_id,
            "library_state": "ACTIVE",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Manual protection applied to all 51 filtered lifecycles" in response.text
    with app.state.database.session_factory() as session:
        lifecycles = {item.id: item for item in session.scalars(select(MediaLifecycle)).all()}
        assert all(
            "MANUAL_SELECTION" in lifecycles[lifecycle_id].protection_sources
            for lifecycle_id in active_sonarr_ids
        )
        assert "MANUAL_SELECTION" not in lifecycles[missing_id].protection_sources
        assert "MANUAL_SELECTION" not in lifecycles[other_source_id].protection_sources
        events = session.scalars(
            select(EventRecord).where(
                EventRecord.event_type.in_(
                    ("media.manual_protection_changed", "media.manual_protection_batch_changed")
                )
            )
        ).all()
        assert len(events) == 52
        assert len({event.correlation_id for event in events}) == 1
        batch = next(
            event for event in events if event.event_type == "media.manual_protection_batch_changed"
        )
        assert batch.payload["selection_scope"] == "filtered"
        assert batch.payload["matched"] == 51
        assert batch.payload["changed"] == 51
