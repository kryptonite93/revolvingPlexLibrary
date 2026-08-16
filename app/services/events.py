from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.persistence.models import EventRecord
from app.security.redaction import redact


def append_event(
    session: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str | None,
    actor_type: str,
    actor_id: str | None,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> EventRecord:
    record = EventRecord(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_type=actor_type,
        actor_id=actor_id,
        correlation_id=correlation_id or str(uuid.uuid4()),
        payload=redact(payload or {}),
    )
    session.add(record)
    session.flush()
    return record
