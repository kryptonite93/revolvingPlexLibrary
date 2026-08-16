from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.base import ConnectionTestResult
from app.integrations.urls import normalize_base_url


class TautulliAdapter:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = api_key
        self._client_factory = client_factory

    def _command(self, command: str, **params: Any) -> Any:
        with self._client_factory(
            base_url=self.base_url,
            timeout=10.0,
            follow_redirects=False,
        ) as client:
            response = client.get(
                "/api/v2",
                params={"apikey": self.api_key, "cmd": command, **params},
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
        envelope = payload.get("response", {}) if isinstance(payload, dict) else {}
        if envelope.get("result") != "success":
            raise ValueError(str(envelope.get("message") or "Tautulli API request failed"))
        return envelope.get("data")

    def test_connection(self) -> ConnectionTestResult:
        data = self._command("get_tautulli_info")
        version = str(data.get("tautulli_version")) if isinstance(data, dict) else None
        return ConnectionTestResult(True, "Authenticated connection succeeded.", version)

    def history(self, *, start: int = 0, length: int = 1000) -> dict[str, Any]:
        data = self._command(
            "get_history",
            grouping=0,
            include_activity=0,
            order_column="date",
            order_dir="asc",
            start=start,
            length=length,
        )
        return data if isinstance(data, dict) else {"data": []}
