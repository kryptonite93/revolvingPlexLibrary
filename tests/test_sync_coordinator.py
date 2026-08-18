from __future__ import annotations

import pytest

from app.services.sync_coordinator import SyncAlreadyRunning, SyncCoordinator


def test_sync_coordinator_exposes_and_rejects_overlapping_activity() -> None:
    coordinator = SyncCoordinator()

    with coordinator.acquire("sonarr-id", "Sonarr-1080P", trigger="scheduled"):
        activity = coordinator.current()
        assert activity is not None
        assert activity.integration_name == "Sonarr-1080P"
        assert activity.trigger == "scheduled"

        with (
            pytest.raises(SyncAlreadyRunning) as conflict,
            coordinator.acquire("overseerr-id", "MediaMule Requests", trigger="manual"),
        ):
            pytest.fail("Overlapping sync unexpectedly acquired the coordinator")

        assert conflict.value.activity.integration_name == "Sonarr-1080P"

    assert coordinator.current() is None
