from __future__ import annotations

from typing import Any

import httpx

from app.integrations.arr import ArrAdapter


def client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=transport, **kwargs)

    return factory


def test_radarr_movie_file_inventory_is_scoped_to_known_movies() -> None:
    movie_file_queries: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/movie"):
            return httpx.Response(200, json=[{"id": 41}, {"id": 42}])
        if path.endswith("/moviefile"):
            movie_ids = request.url.params.get_list("movieId")
            movie_file_queries.append(movie_ids)
            if not movie_ids:
                return httpx.Response(400, json={"message": "movieId is required"})
            return httpx.Response(200, json=[{"id": 7, "movieId": 41}])
        if path.endswith("/history"):
            return httpx.Response(200, json={"totalRecords": 0, "records": []})
        if path.endswith("/tag"):
            return httpx.Response(200, json=[])
        raise AssertionError(f"Unexpected Radarr request: {request.url}")

    payload = ArrAdapter(
        "http://radarr:7878",
        "secret",
        client_factory=client_factory(handler),
    ).inventory("RADARR")

    assert payload["files"] == [{"id": 7, "movieId": 41}]
    assert movie_file_queries == [["41", "42"]]


def test_arr_history_inventory_follows_every_page() -> None:
    requested_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/movie") or path.endswith("/tag"):
            return httpx.Response(200, json=[])
        if path.endswith("/history"):
            page = int(request.url.params["page"])
            requested_pages.append(page)
            records = [{"id": 1}, {"id": 2}] if page == 1 else [{"id": 3}]
            return httpx.Response(
                200,
                json={
                    "page": page,
                    "pageSize": 2,
                    "totalRecords": 3,
                    "records": records,
                },
            )
        raise AssertionError(f"Unexpected Arr request: {request.url}")

    payload = ArrAdapter(
        "http://radarr:7878",
        "secret",
        client_factory=client_factory(handler),
    ).inventory("RADARR")

    assert payload["history"] == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert requested_pages == [1, 2]


def test_radarr_delete_removes_files_without_adding_an_exclusion() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200)

    ArrAdapter(
        "http://radarr:7878",
        "secret",
        client_factory=client_factory(handler),
    ).delete_movie(42)

    assert len(observed) == 1
    request = observed[0]
    assert request.method == "DELETE"
    assert request.url.path == "/api/v3/movie/42"
    assert request.url.params["deleteFiles"] == "true"
    assert request.url.params["addImportExclusion"] == "false"
    assert request.headers["X-Api-Key"] == "secret"
