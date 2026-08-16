from __future__ import annotations

import pytest
from sqlalchemy import select

from app.persistence.models import EventRecord, ImmutableEventError
from app.services.events import append_event


def test_event_payload_is_redacted(app) -> None:
    with app.state.database.session_factory() as session:
        record = append_event(
            session,
            event_type="integration.tested",
            entity_type="integration",
            entity_id="example",
            actor_type="admin",
            actor_id="owner",
            payload={
                "api_key": "secret-value",
                "url": "https://user:password@example.test/api?apikey=secret&safe=yes",
            },
        )
        session.commit()
        stored = session.scalar(select(EventRecord).where(EventRecord.id == record.id))
        assert stored is not None
        assert stored.payload["api_key"] == "[REDACTED]"
        assert "secret" not in stored.payload["url"]
        assert "user:password" not in stored.payload["url"]
        assert "safe=yes" in stored.payload["url"]


def test_event_cannot_be_updated_or_deleted(app) -> None:
    with app.state.database.session_factory() as session:
        record = append_event(
            session,
            event_type="test.created",
            entity_type="test",
            entity_id="one",
            actor_type="system",
            actor_id=None,
        )
        session.commit()
        record.event_type = "test.changed"
        with pytest.raises(ImmutableEventError):
            session.commit()
        session.rollback()
        with pytest.raises(ImmutableEventError):
            session.delete(record)
            session.commit()
