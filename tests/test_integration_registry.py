from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.integrations.base import ConnectionTestResult
from app.persistence.models import IntegrationInstance, ManagedLibrary


def csrf_from(response) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def authenticate(client: TestClient) -> str:
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
    return csrf_from(client.get("/integrations"))


def test_new_integration_is_encrypted_and_disabled(client: TestClient, app) -> None:
    csrf = authenticate(client)
    response = client.post(
        "/integrations",
        data={
            "kind": "OVERSEERR",
            "name": "Requests",
            "base_url": "http://overseerr:5055/",
            "api_key": "not-in-the-page",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.database.session_factory() as session:
        integration = session.scalar(select(IntegrationInstance))
        assert integration is not None
        assert integration.base_url == "http://overseerr:5055"
        assert integration.enabled is False
        assert integration.active_management_enabled is False
        assert "not-in-the-page" not in integration.credentials_encrypted
    page = client.get("/integrations")
    assert "not-in-the-page" not in page.text
    assert "Disabled" in page.text


def test_enable_switch_is_explicit_and_audited(client: TestClient, app) -> None:
    csrf = authenticate(client)
    client.post(
        "/integrations",
        data={
            "kind": "RADARR",
            "name": "Movies",
            "base_url": "http://radarr:7878",
            "api_key": "secret",
            "csrf": csrf,
        },
    )
    with app.state.database.session_factory() as session:
        integration_id = session.scalar(select(IntegrationInstance.id))
    page = client.get("/integrations")
    response = client.post(
        f"/integrations/{integration_id}/enabled",
        data={"csrf": csrf_from(page)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.database.session_factory() as session:
        integration = session.get(IntegrationInstance, integration_id)
        assert integration is not None and integration.enabled is True


def test_failed_connection_uses_error_notice(client: TestClient, app, monkeypatch) -> None:
    csrf = authenticate(client)
    client.post(
        "/integrations",
        data={
            "kind": "OVERSEERR",
            "name": "Requests",
            "base_url": "http://overseerr:5055",
            "api_key": "secret",
            "csrf": csrf,
        },
    )
    with app.state.database.session_factory() as session:
        integration_id = session.scalar(select(IntegrationInstance.id))
    monkeypatch.setattr(
        "app.main.test_integration",
        lambda _integration, _cipher: ConnectionTestResult(False, "failed"),
    )
    page = client.get("/integrations")
    response = client.post(
        f"/integrations/{integration_id}/test",
        data={"csrf": csrf_from(page)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert 'class="notice notice-error" role="alert"' in response.text
    assert "Connection failed" in response.text


def test_qbittorrent_cookie_credentials_are_encrypted(client: TestClient, app) -> None:
    csrf = authenticate(client)
    response = client.post(
        "/integrations",
        data={
            "kind": "QBITTORRENT",
            "name": "Downloads",
            "base_url": "http://qbittorrent:8080",
            "api_key": "",
            "username": "admin",
            "password": "cookie-password",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.database.session_factory() as session:
        integration = session.scalar(select(IntegrationInstance))
        assert integration is not None
        assert integration.active_management_enabled is False
        assert "cookie-password" not in integration.credentials_encrypted
    assert "cookie-password" not in client.get("/integrations").text


def test_managed_mode_requires_explicit_confirmation(client: TestClient, app) -> None:
    csrf = authenticate(client)
    client.post(
        "/integrations",
        data={
            "kind": "RADARR",
            "name": "Movies",
            "base_url": "http://radarr:7878",
            "api_key": "secret",
            "csrf": csrf,
        },
    )
    with app.state.database.session_factory() as session:
        integration_id = session.scalar(select(IntegrationInstance.id))
    page = client.get("/integrations")
    response = client.post(
        f"/integrations/{integration_id}/management-mode",
        data={"management_mode": "MANAGED", "csrf": csrf_from(page)},
        follow_redirects=True,
    )
    assert "Confirm more permissive" in response.text
    page = client.get("/integrations")
    client.post(
        f"/integrations/{integration_id}/management-mode",
        data={
            "management_mode": "MANAGED",
            "confirm_permissive": "yes",
            "csrf": csrf_from(page),
        },
    )
    with app.state.database.session_factory() as session:
        integration = session.get(IntegrationInstance, integration_id)
        assert integration is not None and integration.management_mode == "MANAGED"


def test_plex_library_selection_requires_enabled_plex(client: TestClient, app) -> None:
    csrf = authenticate(client)
    client.post(
        "/integrations",
        data={
            "kind": "PLEX",
            "name": "Home Plex",
            "base_url": "http://plex:32400",
            "api_key": "plex-token",
            "csrf": csrf,
        },
    )
    with app.state.database.session_factory() as session:
        plex = session.scalar(select(IntegrationInstance))
        assert plex is not None
        library = ManagedLibrary(
            plex_integration_id=plex.id,
            external_id="1",
            name="Movies",
            media_type="movie",
            enabled=False,
        )
        session.add(library)
        session.commit()
        library_id = library.id
        plex_id = plex.id
    page = client.get("/integrations")
    response = client.post(
        f"/libraries/{library_id}/enabled",
        data={"csrf": csrf_from(page)},
        follow_redirects=True,
    )
    assert "Enable Plex before selecting libraries" in response.text
    page = client.get("/integrations")
    client.post(
        f"/integrations/{plex_id}/enabled",
        data={"csrf": csrf_from(page)},
    )
    page = client.get("/integrations")
    client.post(
        f"/libraries/{library_id}/enabled",
        data={"csrf": csrf_from(page)},
    )
    with app.state.database.session_factory() as session:
        library = session.get(ManagedLibrary, library_id)
        assert library is not None and library.enabled is True
    page = client.get("/integrations")
    assert "Remove from scope" in page.text
    assert "never deletes content from Plex" in page.text


def test_active_management_route_is_blocked_in_inventory_only(client: TestClient, app) -> None:
    csrf = authenticate(client)
    client.post(
        "/integrations",
        data={
            "kind": "QBITTORRENT",
            "name": "Downloads",
            "base_url": "http://qbittorrent:8080",
            "api_key": "qbt_preview",
            "csrf": csrf,
        },
    )
    with app.state.database.session_factory() as session:
        integration = session.scalar(select(IntegrationInstance))
        assert integration is not None
        integration.enabled = True
        integration.health_status = "HEALTHY"
        integration.last_success_at = datetime.now(UTC)
        integration.full_sync_completed_at = datetime.now(UTC)
        integration.dry_run_evaluated_at = datetime.now(UTC)
        session.commit()
        integration_id = integration.id
    page = client.get("/integrations")
    response = client.post(
        f"/integrations/{integration_id}/active-management",
        data={"csrf": csrf_from(page)},
        follow_redirects=True,
    )
    assert "unavailable while rollout mode is Inventory Only" in response.text
    page = client.get("/integrations")
    assert "Enable Active Management" not in page.text
    client.post(
        f"/integrations/{integration_id}/active-management",
        data={"confirm_active": "yes", "csrf": csrf_from(page)},
    )
    with app.state.database.session_factory() as session:
        integration = session.get(IntegrationInstance, integration_id)
        assert integration is not None and integration.active_management_enabled is False
