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


def test_qbittorrent_cookie_auth_then_reads_version() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path.endswith("/auth/login"):
            assert request.headers["Referer"] == "http://qbittorrent:8080/"
            return httpx.Response(200, text="Ok.", headers={"Set-Cookie": "SID=test; path=/"})
        assert "SID=test" in request.headers["Cookie"]
        return httpx.Response(200, text="v5.1.2")

    result = QBittorrentAdapter(
        "http://qbittorrent:8080",
        {"username": "admin", "password": "password"},
        client_factory(handler),
    ).test_connection()
    assert result.version == "v5.1.2"
    assert methods == ["POST", "GET"]


def test_qbittorrent_api_key_skips_login() -> None:
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
