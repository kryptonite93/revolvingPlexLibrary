from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app.persistence.database import Database
from app.persistence.models import IntegrationInstance, SourceFreshness, utc_now
from app.security.credentials import CredentialCipher
from app.services.events import append_event
from app.services.inventory import sync_integration
from app.services.sync_coordinator import SyncAlreadyRunning, SyncCoordinator

logger = logging.getLogger(__name__)


def run_due_inventory_syncs(
    database: Database,
    cipher: CredentialCipher,
    coordinator: SyncCoordinator | None = None,
) -> int:
    completed = 0
    with database.session_factory() as session:
        integrations = session.scalars(
            select(IntegrationInstance).where(
                IntegrationInstance.enabled.is_(True),
                IntegrationInstance.management_mode != "IGNORED",
            )
        ).all()
        # Non-Arr integrations have a null management mode and need explicit inclusion.
        integrations.extend(
            session.scalars(
                select(IntegrationInstance).where(
                    IntegrationInstance.enabled.is_(True),
                    IntegrationInstance.management_mode.is_(None),
                )
            ).all()
        )
        seen: set[str] = set()
        for integration in integrations:
            if integration.id in seen:
                continue
            seen.add(integration.id)
            source = session.scalar(
                select(SourceFreshness).where(
                    SourceFreshness.integration_id == integration.id,
                    SourceFreshness.source_kind == integration.kind,
                )
            )
            if source and source.last_attempt_at:
                last_attempt = source.last_attempt_at
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=utc_now().tzinfo)
                if utc_now() - last_attempt < timedelta(seconds=source.stale_after_seconds):
                    continue
            try:
                if coordinator is None:
                    run = sync_integration(session, integration, cipher)
                    append_event(
                        session,
                        event_type="inventory.scheduled_sync_completed",
                        entity_type="integration",
                        entity_id=integration.id,
                        actor_type="system",
                        actor_id=None,
                        payload={"status": run.status, "counts": run.counts},
                    )
                    session.commit()
                else:
                    with coordinator.acquire(
                        integration.id, integration.name, trigger="scheduled"
                    ):
                        run = sync_integration(session, integration, cipher)
                        append_event(
                            session,
                            event_type="inventory.scheduled_sync_completed",
                            entity_type="integration",
                            entity_id=integration.id,
                            actor_type="system",
                            actor_id=None,
                            payload={"status": run.status, "counts": run.counts},
                        )
                        session.commit()
                completed += 1
            except SyncAlreadyRunning:
                session.rollback()
                break
    return completed


async def inventory_scheduler_loop(
    database: Database,
    cipher: CredentialCipher,
    *,
    poll_seconds: int,
    coordinator: SyncCoordinator | None = None,
) -> None:
    while True:
        try:
            await asyncio.to_thread(run_due_inventory_syncs, database, cipher, coordinator)
        except Exception:
            logger.error("Scheduled inventory pass failed; retrying after the poll interval")
        await asyncio.sleep(poll_seconds)
