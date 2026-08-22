from __future__ import annotations

import httpx

from app.integrations.plex import PlexAdapter
from app.integrations.qbittorrent import QBittorrentAdapter
from app.integrations.tautulli import TautulliAdapter


def client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return factory


def test_plex_connection_and_library_discovery_are_get_only() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.headers["X-Plex-Token"] == "plex-token"
        assert request.headers["Accept"] == "application/json"
        if request.url.path == "/":
            return httpx.Response(
                200,
                json={"MediaContainer": {"friendlyName": "Home", "version": "1.43.0"}},
            )
        return httpx.Response(
            200,
            json={
                "MediaContainer": {
                    "Directory": [
                        {"key": "1", "title": "Movies", "type": "movie"},
                        {"key": "2", "title": "Television", "type": "show"},
                    ]
                }
            },
        )

    adapter = PlexAdapter("http://plex:32400", "plex-token", client_factory(handler))
    result = adapter.test_connection()
    libraries = adapter.discover_libraries()
    assert result.version == "1.43.0"
    assert [(item.external_id, item.name) for item in libraries] == [
        ("1", "Movies"),
        ("2", "Television"),
    ]
    assert methods == ["GET", "GET"]


def test_plex_inventory_maps_show_children_with_get_only() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == "/library/sections/2/all":
            assert request.url.params["includeGuids"] == "1"
            return httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "Metadata": [
                            {
                                "ratingKey": "show-7",
                                "Guid": [{"id": "tvdb://121361"}],
                            }
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={"MediaContainer": {"Metadata": [{"ratingKey": "season-9", "index": 6}]}},
        )

    rows = PlexAdapter("http://plex:32400", "plex-token", client_factory(handler)).library_items(
        "2", "show"
    )
    assert rows[0]["ratingKey"] == "season-9"
    assert rows[0]["seriesGuids"] == [{"id": "tvdb://121361"}]
    assert methods == ["GET", "GET"]


def test_tautulli_health_uses_read_only_info_command() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.params["apikey"] == "tautulli-key"
        assert request.url.params["cmd"] == "get_tautulli_info"
        return httpx.Response(
            200,
            json={
                "response": {
                    "result": "success",
                    "message": None,
                    "data": {"tautulli_version": "v2.15.3"},
                }
            },
        )

    result = TautulliAdapter(
        "http://tautulli:8181", "tautulli-key", client_factory(handler)
    ).test_connection()
    assert result.version == "v2.15.3"


def test_tautulli_lists_playback_user_names() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["cmd"] == "get_user_names"
        return httpx.Response(
            200,
            json={
                "response": {
                    "result": "success",
                    "message": None,
                    "data": [{"user_id": 7, "friendly_name": "Viewer Name"}],
                }
            },
        )

    users = TautulliAdapter(
        "http://tautulli:8181",
        "secret",
        client_factory=client_factory(handler),
    ).user_names()

    assert users == [{"user_id": 7, "friendly_name": "Viewer Name"}]


def test_qbittorrent_api_key_authenticates_with_bearer_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer qbt_example"
        return httpx.Response(200, text="v5.2.0")

    result = QBittorrentAdapter(
        "http://qbittorrent:8080",
        {"api_key": "qbt_example"},
        client_factory(handler),
    ).test_connection()
    assert result.version == "v5.2.0"


def test_qbittorrent_delete_explicitly_removes_downloaded_files() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200)

    QBittorrentAdapter(
        "http://qbittorrent:8080",
        {"api_key": "qbt_example"},
        client_factory(handler),
    ).delete_torrent("abc123", delete_files=True)

    assert len(observed) == 1
    request = observed[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v2/torrents/delete"
    assert request.headers["Authorization"] == "Bearer qbt_example"
    assert request.content == b"hashes=abc123&deleteFiles=true"


def test_plex_execution_checks_sessions_then_refreshes_one_library() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/status/sessions":
            return httpx.Response(
                200,
                json={
                    "MediaContainer": {
                        "Metadata": [{"ratingKey": "episode", "grandparentRatingKey": "show"}]
                    }
                },
            )
        return httpx.Response(200, json={"MediaContainer": {}})

    adapter = PlexAdapter("http://plex:32400", "plex-token", client_factory(handler))
    assert adapter.active_session_rating_keys() == {"episode", "show"}
    adapter.refresh_library("1")

    assert paths == ["/status/sessions", "/library/sections/1/refresh"]


def test_plex_refresh_accepts_a_successful_empty_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/library/sections/1/refresh"
        return httpx.Response(200, content=b"")

    PlexAdapter(
        "http://plex:32400",
        "plex-token",
        client_factory=client_factory(handler),
    ).refresh_library("1")
