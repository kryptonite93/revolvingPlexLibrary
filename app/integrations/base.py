from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConnectionTestResult:
    healthy: bool
    detail: str
    version: str | None = None


@dataclass(frozen=True)
class DiscoveredIntegration:
    kind: str
    name: str
    base_url: str
    api_key: str
    external_id: str | None = None


@dataclass(frozen=True)
class DiscoveredLibrary:
    external_id: str
    name: str
    media_type: str


class RequestSystemAdapter(Protocol):
    def test_connection(self) -> ConnectionTestResult: ...

    def discover_arr_instances(self) -> list[DiscoveredIntegration]: ...
