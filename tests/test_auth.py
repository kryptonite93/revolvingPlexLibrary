from __future__ import annotations

import re

from fastapi.testclient import TestClient


def csrf_from(response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def test_setup_creates_admin_and_authenticates(client: TestClient) -> None:
    setup = client.get("/setup")
    response = client.post(
        "/setup",
        data={
            "username": "owner",
            "password": "a-secure-password",
            "password_confirm": "a-secure-password",
            "csrf": csrf_from(setup),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert "INVENTORY_ONLY" in dashboard.text


def test_setup_rejects_missing_csrf(client: TestClient) -> None:
    response = client.post(
        "/setup",
        data={
            "username": "owner",
            "password": "a-secure-password",
            "password_confirm": "a-secure-password",
            "csrf": "invalid",
        },
    )
    assert response.status_code == 403


def test_setup_associates_errors_with_relevant_fields(client: TestClient) -> None:
    setup = client.get("/setup")
    response = client.post(
        "/setup",
        data={
            "username": "owner",
            "password": "short",
            "password_confirm": "different",
            "csrf": csrf_from(setup),
        },
    )
    assert response.status_code == 422
    assert 'id="password-error"' in response.text
    assert 'aria-describedby="password-help password-error"' in response.text
    assert 'id="password-confirm-error"' in response.text
    assert 'id="username-error"' not in response.text


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/setup")
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
