from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.base import ConnectionTestResult
from app.integrations.urls import normalize_base_url


class QBittorrentAdapter:
    def __init__(
        self,
        base_url: str,
        credentials: dict[str, str],
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.credentials = credentials
        self._client_factory = client_factory

    def test_connection(self) -> ConnectionTestResult:
        api_key = self.credentials.get("api_key", "")
        headers = {"Accept": "text/plain"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers["Referer"] = f"{self.base_url}/"

        with self._client_factory(
            base_url=self.base_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            if not api_key:
                username = self.credentials.get("username", "")
                password = self.credentials.get("password", "")
                response = client.post(
                    "/api/v2/auth/login",
                    data={"username": username, "password": password},
                )
                response.raise_for_status()
                if response.text.strip() != "Ok.":
                    raise ValueError("qBittorrent rejected the saved credentials")
            version_response = client.get("/api/v2/app/version")
            version_response.raise_for_status()
            version = version_response.text.strip()
        return ConnectionTestResult(True, "Authenticated connection succeeded.", version)

    def inventory(self) -> list[dict[str, Any]]:
        """Read torrents and trackers without exposing qBittorrent mutation calls."""
        api_key = self.credentials.get("api_key", "")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers["Referer"] = f"{self.base_url}/"
        with self._client_factory(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            if not api_key:
                response = client.post(
                    "/api/v2/auth/login",
                    data={
                        "username": self.credentials.get("username", ""),
                        "password": self.credentials.get("password", ""),
                    },
                )
                response.raise_for_status()
                if response.text.strip() != "Ok.":
                    raise ValueError("qBittorrent rejected the saved credentials")
            response = client.get("/api/v2/torrents/info", params={"filter": "all"})
            response.raise_for_status()
            torrents = response.json()
            if not isinstance(torrents, list):
                raise ValueError("qBittorrent returned an invalid torrent list")
            for torrent in torrents:
                tracker_response = client.get(
                    "/api/v2/torrents/trackers", params={"hash": torrent.get("hash")}
                )
                tracker_response.raise_for_status()
                torrent["trackers"] = tracker_response.json()
            return torrents
