from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.integrations.base import ConnectionTestResult
from app.persistence.models import EventRecord, IntegrationInstance, ManagedLibrary, SourceFreshness


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
    return csrf_from(client.get("/settings"))


def test_settings_is_the_last_configuration_destination(client: TestClient) -> None:
    authenticate(client)
    page = client.get("/settings")

    assert page.status_code == 200
    assert "<title>Settings · Revolving Plex Manager</title>" in page.text
    assert 'href="/settings" aria-current="page">Settings</a>' in page.text
    assert page.text.index(">Media</a>") < page.text.index(">Deletion queue</a>")
    assert page.text.index(">Deletion queue</a>") < page.text.index(">Audit log</span>")
    assert page.text.index(">Audit log</span>") < page.text.index(">Settings</a>")
    assert "Retention and freshness" in page.text
    assert "Connected services" in page.text

    legacy_page = client.get("/integrations")
    assert legacy_page.status_code == 200
    assert "<h1>Settings</h1>" in legacy_page.text


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


def test_integration_timestamp_uses_configured_timezone(client: TestClient, app) -> None:
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
        integration = session.scalar(select(IntegrationInstance))
        assert integration is not None
        integration.last_success_at = datetime(2026, 8, 17, 5, 30, tzinfo=UTC)
        session.commit()

    page = client.get("/integrations")

    assert "Last success 2026-08-17 01:30 EDT" in page.text
    assert "2026-08-17 05:30 UTC" not in page.text


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


def test_qbittorrent_api_key_is_required_and_encrypted(client: TestClient, app) -> None:
    csrf = authenticate(client)
    rejected = client.post(
        "/integrations",
        data={
            "kind": "QBITTORRENT",
            "name": "Downloads",
            "base_url": "http://qbittorrent:8080",
            "api_key": "",
            "csrf": csrf,
        },
    )
    assert rejected.status_code == 422
    assert "qBittorrent API key is required" in rejected.text

    response = client.post(
        "/integrations",
        data={
            "kind": "QBITTORRENT",
            "name": "Downloads",
            "base_url": "http://qbittorrent:8080",
            "api_key": "qbt_secret_key",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.database.session_factory() as session:
        integration = session.scalar(select(IntegrationInstance))
        assert integration is not None
        assert integration.active_management_enabled is False
        assert "qbt_secret_key" not in integration.credentials_encrypted
        credentials = app.state.credential_cipher.decrypt(integration.credentials_encrypted)
        assert credentials == {"api_key": "qbt_secret_key"}
    page = client.get("/integrations")
    assert "qbt_secret_key" not in page.text
    assert "cookie login" not in page.text
    assert 'name="username"' not in page.text
    assert 'name="password"' not in page.text


def test_integration_credentials_can_be_replaced_without_redisplaying_secret(
    client: TestClient, app
) -> None:
    csrf = authenticate(client)
    client.post(
        "/integrations",
        data={
            "kind": "QBITTORRENT",
            "name": "Downloads",
            "base_url": "http://qbittorrent:8080",
            "api_key": "qbt_wrong_key",
            "csrf": csrf,
        },
    )
    with app.state.database.session_factory() as session:
        integration_id = session.scalar(select(IntegrationInstance.id))

    edit_page = client.get(f"/integrations/{integration_id}/edit")
    assert edit_page.status_code == 200
    assert "qbt_wrong_key" not in edit_page.text
    assert 'value="Downloads"' in edit_page.text

    response = client.post(
        f"/integrations/{integration_id}/edit",
        data={
            "name": "Downloads",
            "base_url": "http://qbittorrent:8080",
            "api_key": "qbt_correct_key",
            "csrf": csrf_from(edit_page),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with app.state.database.session_factory() as session:
        integration = session.get(IntegrationInstance, integration_id)
        assert integration is not None
        credentials = app.state.credential_cipher.decrypt(integration.credentials_encrypted)
        assert credentials == {"api_key": "qbt_correct_key"}


def test_integration_removal_requires_name_and_deletes_only_local_records(
    client: TestClient, app
) -> None:
    csrf = authenticate(client)
    client.post(
        "/integrations",
        data={
            "kind": "QBITTORRENT",
            "name": "Downloads",
            "base_url": "http://qbittorrent:8080",
            "api_key": "qbt_removal_key",
            "csrf": csrf,
        },
    )
    with app.state.database.session_factory() as session:
        integration = session.scalar(select(IntegrationInstance))
        assert integration is not None
        integration_id = integration.id
        session.add(
            SourceFreshness(
                integration_id=integration_id,
                source_kind="QBITTORRENT",
                stale_after_seconds=900,
            )
        )
        session.commit()

    edit_page = client.get(f"/integrations/{integration_id}/edit")
    rejected = client.post(
        f"/integrations/{integration_id}/delete",
        data={"confirm_name": "not-the-name", "csrf": csrf_from(edit_page)},
    )
    assert rejected.status_code == 422
    with app.state.database.session_factory() as session:
        assert session.get(IntegrationInstance, integration_id) is not None

    accepted = client.post(
        f"/integrations/{integration_id}/delete",
        data={"confirm_name": "Downloads", "csrf": csrf_from(rejected)},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    with app.state.database.session_factory() as session:
        assert session.get(IntegrationInstance, integration_id) is None
        assert (
            session.scalar(
                select(SourceFreshness).where(SourceFreshness.integration_id == integration_id)
            )
            is None
        )
        event = session.scalar(
            select(EventRecord).where(EventRecord.event_type == "integration.removed")
        )
        assert event is not None
        assert event.entity_id == integration_id


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
    assert "Confirm mode change" in response.text
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
    assert "Active Management requires the Approval Required rollout mode" in response.text
    page = client.get("/integrations")
    assert "Enable Active Management" not in page.text
    client.post(
        f"/integrations/{integration_id}/active-management",
        data={"confirm_active": "yes", "csrf": csrf_from(page)},
    )
    with app.state.database.session_factory() as session:
        integration = session.get(IntegrationInstance, integration_id)
        assert integration is not None and integration.active_management_enabled is False
