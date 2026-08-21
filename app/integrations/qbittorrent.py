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
        if not api_key:
            raise ValueError("qBittorrent API key is required")
        headers = {"Accept": "text/plain", "Authorization": f"Bearer {api_key}"}

        with self._client_factory(
            base_url=self.base_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            version_response = client.get("/api/v2/app/version")
            version_response.raise_for_status()
            version = version_response.text.strip()
        return ConnectionTestResult(True, "Authenticated connection succeeded.", version)

    def inventory(self) -> list[dict[str, Any]]:
        """Read torrents and trackers."""
        api_key = self.credentials.get("api_key", "")
        if not api_key:
            raise ValueError("qBittorrent API key is required")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
        with self._client_factory(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=False,
        ) as client:
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

    def torrent(self, info_hash: str) -> dict[str, Any] | None:
        api_key = self.credentials.get("api_key", "")
        if not api_key:
            raise ValueError("qBittorrent API key is required")
        with self._client_factory(
            base_url=self.base_url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            response = client.get("/api/v2/torrents/info", params={"hashes": info_hash})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("qBittorrent returned invalid torrent data")
        torrent = next((row for row in payload if isinstance(row, dict)), None)
        if torrent is None:
            return None
        with self._client_factory(
            base_url=self.base_url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            tracker_response = client.get(
                "/api/v2/torrents/trackers", params={"hash": info_hash}
            )
            tracker_response.raise_for_status()
            trackers = tracker_response.json()
        if not isinstance(trackers, list):
            raise ValueError("qBittorrent returned invalid tracker data")
        torrent["trackers"] = trackers
        return torrent

    def delete_torrent(self, info_hash: str, *, delete_files: bool = True) -> None:
        api_key = self.credentials.get("api_key", "")
        if not api_key:
            raise ValueError("qBittorrent API key is required")
        with self._client_factory(
            base_url=self.base_url,
            headers={"Accept": "text/plain", "Authorization": f"Bearer {api_key}"},
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            response = client.post(
                "/api/v2/torrents/delete",
                data={
                    "hashes": info_hash,
                    "deleteFiles": "true" if delete_files else "false",
                },
            )
            response.raise_for_status()
