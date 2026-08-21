from __future__ import annotations

import pytest

from app.persistence.models import RolloutPolicy
from app.services.rollout import change_rollout_mode


def test_rollout_must_advance_one_confirmed_stage_at_a_time() -> None:
    policy = RolloutPolicy(id="default", mode="INVENTORY_ONLY")

    with pytest.raises(ValueError, match="cannot be skipped"):
        change_rollout_mode(policy, "APPROVAL_REQUIRED", confirmed=True)
    with pytest.raises(ValueError, match="Confirm the rollout mode change"):
        change_rollout_mode(policy, "DRY_RUN", confirmed=False)

    assert change_rollout_mode(policy, "DRY_RUN", confirmed=True) == (
        "INVENTORY_ONLY",
        "DRY_RUN",
    )
    assert change_rollout_mode(policy, "APPROVAL_REQUIRED", confirmed=True) == (
        "DRY_RUN",
        "APPROVAL_REQUIRED",
    )


def test_rollout_can_return_to_a_safer_stage_without_confirmation() -> None:
    policy = RolloutPolicy(id="default", mode="APPROVAL_REQUIRED")

    assert change_rollout_mode(policy, "INVENTORY_ONLY", confirmed=False) == (
        "APPROVAL_REQUIRED",
        "INVENTORY_ONLY",
    )
