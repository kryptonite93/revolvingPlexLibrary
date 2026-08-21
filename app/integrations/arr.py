from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app.integrations.base import ConnectionTestResult
from app.integrations.urls import normalize_base_url


class ArrAdapter:
    _MOVIE_FILE_BATCH_SIZE = 100
    _HISTORY_PAGE_SIZE = 10000

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
        """Read one Arr API resource."""
        with self._client_factory(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def movie(self, movie_id: int) -> dict[str, Any] | None:
        try:
            payload = self.get_json(f"/api/v3/movie/{movie_id}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                return None
            raise
        if not isinstance(payload, dict):
            raise ValueError("Radarr returned invalid movie data")
        return payload

    def delete_movie(self, movie_id: int, *, add_import_exclusion: bool = True) -> None:
        with self._client_factory(
            base_url=self.base_url,
            headers={"X-Api-Key": self.api_key, "Accept": "application/json"},
            timeout=60.0,
            follow_redirects=False,
        ) as client:
            response = client.delete(
                f"/api/v3/movie/{movie_id}",
                params={
                    "deleteFiles": "true",
                    "addImportExclusion": str(add_import_exclusion).lower(),
                },
            )
            response.raise_for_status()

    def ensure_import_exclusion(self, tmdb_id: int, title: str, year: int | None) -> bool:
        payload = self.get_json("/api/v3/exclusions")
        if not isinstance(payload, list):
            raise ValueError("Radarr returned invalid import-exclusion data")
        if any(
            isinstance(item, dict) and int(item.get("tmdbId") or 0) == tmdb_id
            for item in payload
        ):
            return False
        with self._client_factory(
            base_url=self.base_url,
            headers={
                "X-Api-Key": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
            follow_redirects=False,
        ) as client:
            response = client.post(
                "/api/v3/exclusions",
                json={"tmdbId": tmdb_id, "movieTitle": title, "movieYear": year or 0},
            )
            response.raise_for_status()
        return True

    def _movie_files(self, movies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        movie_ids = [int(item["id"]) for item in movies if item.get("id") is not None]
        files: list[dict[str, Any]] = []
        for start in range(0, len(movie_ids), self._MOVIE_FILE_BATCH_SIZE):
            payload = self.get_json(
                "/api/v3/moviefile",
                {"movieId": movie_ids[start : start + self._MOVIE_FILE_BATCH_SIZE]},
            )
            if not isinstance(payload, list):
                raise ValueError("Radarr returned invalid movie-file data")
            files.extend(item for item in payload if isinstance(item, dict))
        return files

    def _history(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self.get_json(
                "/api/v3/history",
                {
                    "page": page,
                    "pageSize": self._HISTORY_PAGE_SIZE,
                    "sortKey": "date",
                    "sortDirection": "descending",
                },
            )
            if not isinstance(payload, dict):
                raise ValueError("Arr returned invalid history data")
            batch = payload.get("records", [])
            if not isinstance(batch, list):
                raise ValueError("Arr returned invalid history records")
            records.extend(item for item in batch if isinstance(item, dict))
            total_records = int(payload.get("totalRecords") or len(records))
            if not batch or len(records) >= total_records:
                return records
            page += 1

    def inventory(self, kind: str) -> dict[str, Any]:
        if kind == "RADARR":
            movies = self.get_json("/api/v3/movie")
            if not isinstance(movies, list):
                raise ValueError("Radarr returned invalid movie data")
            return {
                "items": movies,
                "files": self._movie_files(movies),
                "history": self._history(),
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
                "history": self._history(),
                "tags": self.get_json("/api/v3/tag"),
            }
        raise ValueError("Arr inventory kind must be RADARR or SONARR")
