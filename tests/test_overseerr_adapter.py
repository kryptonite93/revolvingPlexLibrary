from __future__ import annotations

import httpx

from app.integrations.overseerr import OverseerrAdapter


def client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.Client(transport=transport, **kwargs)

    return factory


def test_overseerr_connection_uses_get_only_and_authenticates_settings() -> None:
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"version": "1.34.0"})
        assert request.headers["X-Api-Key"] == "secret"
        return httpx.Response(200, json={"applicationUrl": ""})

    result = OverseerrAdapter(
        "http://overseerr:5055", "secret", client_factory(handler)
    ).test_connection()
    assert result.healthy is True
    assert result.version == "1.34.0"
    assert methods == ["GET", "GET"]


def test_overseerr_discovers_arrs_with_safe_defaults_left_to_registry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/radarr"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 3,
                        "name": "Movies",
                        "hostname": "radarr",
                        "port": 7878,
                        "useSsl": False,
                        "apiKey": "radarr-key",
                        "baseUrl": "",
                    }
                ],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 4,
                    "name": "Series",
                    "hostname": "sonarr",
                    "port": 8989,
                    "useSsl": False,
                    "apiKey": "sonarr-key",
                    "baseUrl": "/sonarr",
                }
            ],
        )

    discovered = OverseerrAdapter(
        "http://overseerr:5055", "secret", client_factory(handler)
    ).discover_arr_instances()
    assert [(item.kind, item.base_url) for item in discovered] == [
        ("RADARR", "http://radarr:7878"),
        ("SONARR", "http://sonarr:8989/sonarr"),
    ]
