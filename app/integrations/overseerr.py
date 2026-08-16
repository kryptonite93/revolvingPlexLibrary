from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.base import ConnectionTestResult, DiscoveredIntegration
from app.integrations.urls import normalize_base_url, url_from_arr_settings


class OverseerrAdapter:
    """Read-only Overseerr 1.34 adapter.

    Discovery deliberately uses only GET settings endpoints. No request or availability
    state is mutated by this adapter.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self._client_factory = client_factory

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        with self._client_factory(
            base_url=f"{self.base_url}/api/v1",
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def test_connection(self) -> ConnectionTestResult:
        status = self._get("/status")
        version = (
            str(status.get("version"))
            if isinstance(status, dict) and status.get("version")
            else None
        )
        self._get("/settings/main")
        return ConnectionTestResult(True, "Authenticated connection succeeded.", version)

    def discover_arr_instances(self) -> list[DiscoveredIntegration]:
        discovered: list[DiscoveredIntegration] = []
        for kind, path in (("RADARR", "/settings/radarr"), ("SONARR", "/settings/sonarr")):
            payload = self._get(path)
            if not isinstance(payload, list):
                continue
            for item in payload:
                if not isinstance(item, dict) or not item.get("apiKey"):
                    continue
                discovered.append(
                    DiscoveredIntegration(
                        kind=kind,
                        name=str(item.get("name") or kind.title()),
                        base_url=url_from_arr_settings(item),
                        api_key=str(item["apiKey"]),
                        external_id=str(item["id"]) if item.get("id") is not None else None,
                    )
                )
        return discovered

    def requests(self) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        skip = 0
        take = 100
        while True:
            payload = self._get("/request", {"take": take, "skip": skip, "sort": "added"})
            page = payload.get("results", []) if isinstance(payload, dict) else []
            if not isinstance(page, list):
                raise ValueError("Overseerr returned an invalid request list")
            collected.extend(item for item in page if isinstance(item, dict))
            if len(page) < take:
                return collected
            skip += take
