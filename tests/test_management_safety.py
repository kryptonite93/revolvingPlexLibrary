from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.persistence.models import IntegrationInstance
from app.services.integrations import change_management_mode, set_active_management


def integration(kind: str, mode: str | None = None) -> IntegrationInstance:
    return IntegrationInstance(
        kind=kind,
        name="Test",
        base_url="http://service",
        credentials_encrypted="encrypted",
        enabled=True,
        management_mode=mode,
        health_status="HEALTHY",
        last_success_at=datetime.now(UTC),
    )


def test_more_permissive_arr_mode_requires_confirmation() -> None:
    instance = integration("RADARR", "PROTECTED")
    with pytest.raises(ValueError, match="Confirm mode change"):
        change_management_mode(instance, "MANAGED", confirmed=False)
    assert instance.management_mode == "PROTECTED"
    change_management_mode(instance, "MANAGED", confirmed=True)
    assert instance.management_mode == "MANAGED"


def test_less_permissive_mode_disables_active_management() -> None:
    instance = integration("RADARR", "MANAGED")
    instance.active_management_enabled = True
    change_management_mode(instance, "PROTECTED", confirmed=False)
    assert instance.active_management_enabled is False


def test_active_management_requires_sync_and_dry_run() -> None:
    instance = integration("RADARR", "MANAGED")
    with pytest.raises(ValueError, match="full inventory sync"):
        set_active_management(instance, enabled=True)
    instance.full_sync_completed_at = datetime.now(UTC)
    with pytest.raises(ValueError, match="dry-run"):
        set_active_management(instance, enabled=True)
    instance.dry_run_evaluated_at = datetime.now(UTC)
    set_active_management(instance, enabled=True)
    assert instance.active_management_enabled is True


def test_disabling_active_management_is_always_allowed() -> None:
    instance = integration("QBITTORRENT")
    instance.enabled = False
    instance.active_management_enabled = True
    set_active_management(instance, enabled=False)
    assert instance.active_management_enabled is False
