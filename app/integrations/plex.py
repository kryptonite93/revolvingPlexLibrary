from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.base import ConnectionTestResult, DiscoveredLibrary
from app.integrations.urls import normalize_base_url


class PlexAdapter:
    def __init__(
        self,
        base_url: str,
        token: str,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self._client_factory = client_factory

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._client_factory(
            base_url=self.base_url,
            headers={
                "X-Plex-Token": self.token,
                "X-Plex-Client-Identifier": "revolving-plex-manager",
                "X-Plex-Product": "Revolving Plex Manager",
                "Accept": "application/json",
            },
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def test_connection(self) -> ConnectionTestResult:
        payload = self._get("/")
        container = payload.get("MediaContainer", {}) if isinstance(payload, dict) else {}
        version = str(container.get("version")) if container.get("version") else None
        name = str(container.get("friendlyName") or "Plex Media Server")
        return ConnectionTestResult(True, f"Connected to {name}.", version)

    def discover_libraries(self) -> list[DiscoveredLibrary]:
        payload = self._get("/library/sections")
        container = payload.get("MediaContainer", {}) if isinstance(payload, dict) else {}
        directories = container.get("Directory", [])
        if isinstance(directories, dict):
            directories = [directories]
        if not isinstance(directories, list):
            return []
        libraries: list[DiscoveredLibrary] = []
        for item in directories:
            if not isinstance(item, dict) or item.get("key") is None:
                continue
            libraries.append(
                DiscoveredLibrary(
                    external_id=str(item["key"]),
                    name=str(item.get("title") or f"Library {item['key']}"),
                    media_type=str(item.get("type") or "unknown"),
                )
            )
        return libraries

    def library_items(self, section_id: str, media_type: str) -> list[dict[str, Any]]:
        payload = self._get(f"/library/sections/{section_id}/all", {"includeGuids": 1})
        container = payload.get("MediaContainer", {}) if isinstance(payload, dict) else {}
        metadata = container.get("Metadata", [])
        if isinstance(metadata, dict):
            metadata = [metadata]
        if not isinstance(metadata, list):
            return []
        if media_type != "show":
            return [item for item in metadata if isinstance(item, dict)]
        seasons: list[dict[str, Any]] = []
        for show in metadata:
            if not isinstance(show, dict) or not show.get("ratingKey"):
                continue
            children = self._get(f"/library/metadata/{show['ratingKey']}/children")
            child_container = (
                children.get("MediaContainer", {}) if isinstance(children, dict) else {}
            )
            child_metadata = child_container.get("Metadata", [])
            if isinstance(child_metadata, dict):
                child_metadata = [child_metadata]
            for season in child_metadata if isinstance(child_metadata, list) else []:
                if isinstance(season, dict):
                    season["seriesGuids"] = show.get("Guid", [])
                    seasons.append(season)
        return seasons
