from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from threading import Lock

from app.persistence.models import utc_now


@dataclass(frozen=True)
class SyncActivity:
    integration_id: str
    integration_name: str
    trigger: str
    started_at: datetime


class SyncAlreadyRunning(RuntimeError):
    def __init__(self, activity: SyncActivity) -> None:
        self.activity = activity
        super().__init__(f"Inventory sync already running for {activity.integration_name}")


class SyncCoordinator:
    """Serialize inventory writers inside the single application process."""

    def __init__(self) -> None:
        self._guard = Lock()
        self._active: SyncActivity | None = None

    def current(self) -> SyncActivity | None:
        with self._guard:
            return self._active

    @contextmanager
    def acquire(
        self,
        integration_id: str,
        integration_name: str,
        *,
        trigger: str,
    ) -> Iterator[SyncActivity]:
        with self._guard:
            if self._active is not None:
                raise SyncAlreadyRunning(self._active)
            activity = SyncActivity(
                integration_id=integration_id,
                integration_name=integration_name,
                trigger=trigger,
                started_at=utc_now(),
            )
            self._active = activity
        try:
            yield activity
        finally:
            with self._guard:
                if self._active is activity:
                    self._active = None
