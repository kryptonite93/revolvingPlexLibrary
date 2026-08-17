from __future__ import annotations

import threading

from sqlalchemy import text

from app.persistence.models import IntegrationInstance, InventoryPolicy, ManagedLibrary
from app.services.events import append_event
from app.services.inventory import sync_integration


def test_remote_inventory_fetch_does_not_hold_sqlite_write_lock(app, monkeypatch) -> None:
    with app.state.database.session_factory() as session:
        integration = IntegrationInstance(
            kind="PLEX",
            name="Plex",
            base_url="http://plex:32400",
            enabled=True,
            credentials_encrypted=app.state.credential_cipher.encrypt({"api_key": "secret"}),
        )
        session.add_all([integration, InventoryPolicy(id="default")])
        session.flush()
        session.add(
            ManagedLibrary(
                plex_integration_id=integration.id,
                external_id="1",
                name="Movies",
                media_type="movie",
                enabled=True,
            )
        )
        session.commit()
        integration_id = integration.id

    fetch_started = threading.Event()
    release_fetch = threading.Event()
    worker_errors: list[Exception] = []

    def blocked_library_fetch(*_args, **_kwargs):
        fetch_started.set()
        if not release_fetch.wait(timeout=3):
            raise TimeoutError("Test did not release the simulated Plex request")
        return []

    monkeypatch.setattr(
        "app.services.inventory.PlexAdapter.library_items",
        blocked_library_fetch,
    )

    def run_sync() -> None:
        try:
            with app.state.database.session_factory() as session:
                integration = session.get(IntegrationInstance, integration_id)
                assert integration is not None
                sync_integration(session, integration, app.state.credential_cipher)
                session.commit()
        except Exception as error:  # pragma: no cover - asserted in the parent thread
            worker_errors.append(error)

    worker = threading.Thread(target=run_sync)
    worker.start()
    assert fetch_started.wait(timeout=1)
    try:
        with app.state.database.session_factory() as session:
            session.execute(text("PRAGMA busy_timeout=100"))
            append_event(
                session,
                event_type="inventory.preview_completed",
                entity_type="integration",
                entity_id=integration_id,
                actor_type="admin",
                actor_id="test-admin",
                payload={"counts": {"plex_items": 1}},
            )
            session.commit()
    finally:
        release_fetch.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert worker_errors == []
