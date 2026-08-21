from __future__ import annotations

from sqlalchemy.orm import Session

from app.persistence.models import RolloutPolicy

ROLLOUT_MODES = ("INVENTORY_ONLY", "DRY_RUN", "APPROVAL_REQUIRED")
_MODE_RANK = {mode: rank for rank, mode in enumerate(ROLLOUT_MODES)}


def get_rollout_policy(session: Session) -> RolloutPolicy:
    policy = session.get(RolloutPolicy, "default")
    if policy is None:
        policy = RolloutPolicy(id="default", mode="INVENTORY_ONLY")
        session.add(policy)
        session.flush()
    return policy


def change_rollout_mode(
    policy: RolloutPolicy,
    target: str,
    *,
    confirmed: bool,
) -> tuple[str, str]:
    normalized = target.strip().upper()
    if normalized not in ROLLOUT_MODES:
        raise ValueError("Choose Inventory Only, Dry Run, or Approval Required.")
    previous = policy.mode
    if normalized == previous:
        return previous, normalized
    if _MODE_RANK[normalized] > _MODE_RANK[previous]:
        if _MODE_RANK[normalized] != _MODE_RANK[previous] + 1:
            raise ValueError("Rollout stages cannot be skipped.")
        if not confirmed:
            raise ValueError("Confirm the rollout mode change.")
    policy.mode = normalized
    return previous, normalized
