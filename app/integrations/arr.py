from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.base import ConnectionTestResult
from app.integrations.urls import normalize_base_url


class ArrAdapter:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self._client_factory = client_factory

    def test_connection(self) -> ConnectionTestResult:
        with self._client_factory(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            response = client.get("/api/v3/system/status")
            response.raise_for_status()
            payload = response.json()
        version = (
            str(payload.get("version"))
            if isinstance(payload, dict) and payload.get("version")
            else None
        )
        return ConnectionTestResult(True, "Read-only connection succeeded.", version)

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Read one Arr API resource. This adapter intentionally has no mutation methods."""
        with self._client_factory(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def inventory(self, kind: str) -> dict[str, Any]:
        history_params = {
            "page": 1,
            "pageSize": 10000,
            "sortKey": "date",
            "sortDirection": "descending",
        }
        if kind == "RADARR":
            return {
                "items": self.get_json("/api/v3/movie"),
                "files": self.get_json("/api/v3/moviefile"),
                "history": self.get_json("/api/v3/history", history_params),
                "tags": self.get_json("/api/v3/tag"),
            }
        if kind == "SONARR":
            series = self.get_json("/api/v3/series")
            files: list[dict[str, Any]] = []
            episodes: list[dict[str, Any]] = []
            for item in series if isinstance(series, list) else []:
                series_id = item.get("id")
                if series_id is not None:
                    payload = self.get_json("/api/v3/episodefile", {"seriesId": series_id})
                    if isinstance(payload, list):
                        files.extend(payload)
                    episode_payload = self.get_json("/api/v3/episode", {"seriesId": series_id})
                    if isinstance(episode_payload, list):
                        episodes.extend(episode_payload)
            return {
                "items": series,
                "files": files,
                "episodes": episodes,
                "history": self.get_json("/api/v3/history", history_params),
                "tags": self.get_json("/api/v3/tag"),
            }
        raise ValueError("Arr inventory kind must be RADARR or SONARR")
