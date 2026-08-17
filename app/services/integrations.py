from __future__ import annotations

from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.integrations.arr import ArrAdapter
from app.integrations.base import ConnectionTestResult
from app.integrations.overseerr import OverseerrAdapter
from app.integrations.plex import PlexAdapter
from app.integrations.qbittorrent import QBittorrentAdapter
from app.integrations.tautulli import TautulliAdapter
from app.integrations.urls import normalize_base_url
from app.persistence.models import (
    IntegrationInstance,
    ManagedLibrary,
    MediaFileRevision,
    MediaLifecycle,
    Playback,
    RequestRecord,
    SourceFreshness,
    SyncRun,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
)
from app.security.credentials import CredentialCipher
from app.security.redaction import redact

SUPPORTED_KINDS = {"OVERSEERR", "RADARR", "SONARR", "PLEX", "TAUTULLI", "QBITTORRENT"}
ARR_KINDS = {"RADARR", "SONARR"}
ACTIVE_MANAGEMENT_KINDS = ARR_KINDS | {"QBITTORRENT"}
MANAGEMENT_MODES = {"IGNORED", "PROTECTED", "MANAGED"}
_MODE_RANK = {"IGNORED": 0, "PROTECTED": 1, "MANAGED": 2}


def create_integration(
    session: Session,
    cipher: CredentialCipher,
    *,
    kind: str,
    name: str,
    base_url: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
    discovered_from_instance_id: str | None = None,
    external_id: str | None = None,
) -> IntegrationInstance:
    normalized_kind = kind.strip().upper()
    if normalized_kind not in SUPPORTED_KINDS:
        raise ValueError("Unsupported integration kind")
    normalized_url = normalize_base_url(base_url)
    if not name.strip():
        raise ValueError("Name is required")

    credentials: dict[str, str]
    if normalized_kind == "QBITTORRENT":
        if api_key.strip():
            credentials = {"api_key": api_key.strip()}
        elif username.strip() and password:
            credentials = {"username": username.strip(), "password": password}
        else:
            raise ValueError("qBittorrent requires an API key or username and password")
    else:
        if not api_key.strip():
            raise ValueError("API key or token is required")
        credentials = {"api_key": api_key.strip()}

    existing = session.scalar(
        select(IntegrationInstance).where(
            IntegrationInstance.kind == normalized_kind,
            IntegrationInstance.base_url == normalized_url,
        )
    )
    if existing:
        return existing
    integration = IntegrationInstance(
        kind=normalized_kind,
        name=name.strip(),
        base_url=normalized_url,
        enabled=False,
        active_management_enabled=False,
        management_mode="PROTECTED" if normalized_kind in ARR_KINDS else None,
        credentials_encrypted=cipher.encrypt(credentials),
        discovered_from_instance_id=discovered_from_instance_id,
        external_id=external_id,
    )
    session.add(integration)
    session.flush()
    return integration


def update_integration(
    session: Session,
    cipher: CredentialCipher,
    integration: IntegrationInstance,
    *,
    name: str,
    base_url: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
) -> bool:
    normalized_url = normalize_base_url(base_url)
    if not name.strip():
        raise ValueError("Name is required")
    duplicate = session.scalar(
        select(IntegrationInstance).where(
            IntegrationInstance.id != integration.id,
            IntegrationInstance.kind == integration.kind,
            IntegrationInstance.base_url == normalized_url,
        )
    )
    if duplicate:
        raise ValueError("An integration of this type already uses that URL")

    credentials_replaced = False
    credentials = cipher.decrypt(integration.credentials_encrypted)
    if integration.kind == "QBITTORRENT":
        if api_key.strip():
            credentials = {"api_key": api_key.strip()}
            credentials_replaced = True
        elif username.strip() or password:
            if not username.strip() or not password:
                raise ValueError("qBittorrent requires both username and password")
            credentials = {"username": username.strip(), "password": password}
            credentials_replaced = True
    elif api_key.strip():
        credentials = {"api_key": api_key.strip()}
        credentials_replaced = True

    connection_changed = integration.base_url != normalized_url or credentials_replaced
    integration.name = name.strip()
    integration.base_url = normalized_url
    if credentials_replaced:
        integration.credentials_encrypted = cipher.encrypt(credentials)
    if connection_changed:
        integration.enabled = False
        integration.active_management_enabled = False
        integration.health_status = "UNTESTED"
        integration.sanitized_error = None
        integration.last_test_at = None
        integration.last_success_at = None
    return credentials_replaced


def remove_integration_local_data(session: Session, integration: IntegrationInstance) -> None:
    """Remove only this application's records; no connector adapter is called."""
    integration_id = integration.id
    session.execute(
        update(IntegrationInstance)
        .where(IntegrationInstance.discovered_from_instance_id == integration_id)
        .values(discovered_from_instance_id=None)
    )

    library_ids = list(
        session.scalars(
            select(ManagedLibrary.id).where(ManagedLibrary.plex_integration_id == integration_id)
        )
    )
    if library_ids:
        session.execute(
            update(MediaLifecycle)
            .where(MediaLifecycle.library_id.in_(library_ids))
            .values(library_id=None)
        )

    lifecycle_ids = list(
        session.scalars(
            select(MediaLifecycle.id).where(MediaLifecycle.integration_id == integration_id)
        )
    )
    if lifecycle_ids:
        session.execute(
            delete(TorrentMediaMapping).where(TorrentMediaMapping.lifecycle_id.in_(lifecycle_ids))
        )
        session.execute(
            delete(MediaFileRevision).where(MediaFileRevision.lifecycle_id.in_(lifecycle_ids))
        )
        session.execute(delete(MediaLifecycle).where(MediaLifecycle.id.in_(lifecycle_ids)))

    torrent_ids = list(
        session.scalars(select(Torrent.id).where(Torrent.integration_id == integration_id))
    )
    if torrent_ids:
        session.execute(
            delete(TorrentMediaMapping).where(TorrentMediaMapping.torrent_id.in_(torrent_ids))
        )
        session.execute(delete(TorrentTracker).where(TorrentTracker.torrent_id.in_(torrent_ids)))
        session.execute(delete(Torrent).where(Torrent.id.in_(torrent_ids)))

    for model in (Playback, RequestRecord, SyncRun, SourceFreshness):
        session.execute(delete(model).where(model.integration_id == integration_id))
    session.execute(
        delete(ManagedLibrary).where(ManagedLibrary.plex_integration_id == integration_id)
    )
    session.delete(integration)


def _adapter_for(
    integration: IntegrationInstance, credentials: dict[str, str]
) -> OverseerrAdapter | ArrAdapter | PlexAdapter | TautulliAdapter | QBittorrentAdapter:
    if integration.kind == "OVERSEERR":
        return OverseerrAdapter(integration.base_url, credentials["api_key"])
    if integration.kind in ARR_KINDS:
        return ArrAdapter(integration.base_url, credentials["api_key"])
    if integration.kind == "PLEX":
        return PlexAdapter(integration.base_url, credentials["api_key"])
    if integration.kind == "TAUTULLI":
        return TautulliAdapter(integration.base_url, credentials["api_key"])
    if integration.kind == "QBITTORRENT":
        return QBittorrentAdapter(integration.base_url, credentials)
    raise ValueError("Unsupported integration kind")


def test_integration(
    integration: IntegrationInstance, cipher: CredentialCipher
) -> ConnectionTestResult:
    credentials = cipher.decrypt(integration.credentials_encrypted)
    integration.last_test_at = datetime.now(UTC)
    try:
        result = _adapter_for(integration, credentials).test_connection()
    except (httpx.HTTPError, ValueError, KeyError) as error:
        integration.health_status = "UNHEALTHY"
        integration.sanitized_error = str(redact(str(error)))[:1000]
        return ConnectionTestResult(False, integration.sanitized_error)
    integration.health_status = "HEALTHY"
    integration.sanitized_error = None
    integration.last_success_at = integration.last_test_at
    return result


def discover_from_overseerr(
    session: Session, source: IntegrationInstance, cipher: CredentialCipher
) -> list[IntegrationInstance]:
    if source.kind != "OVERSEERR":
        raise ValueError("Discovery requires an Overseerr integration")
    credentials = cipher.decrypt(source.credentials_encrypted)
    adapter = OverseerrAdapter(source.base_url, credentials["api_key"])
    discovered: list[IntegrationInstance] = []
    for item in adapter.discover_arr_instances():
        integration = create_integration(
            session,
            cipher,
            kind=item.kind,
            name=item.name,
            base_url=item.base_url,
            api_key=item.api_key,
            discovered_from_instance_id=source.id,
            external_id=item.external_id,
        )
        discovered.append(integration)
    return discovered


def discover_plex_libraries(
    session: Session, source: IntegrationInstance, cipher: CredentialCipher
) -> list[ManagedLibrary]:
    if source.kind != "PLEX":
        raise ValueError("Library discovery requires a Plex integration")
    if not source.enabled:
        raise ValueError("Enable Plex before discovering libraries")
    credentials = cipher.decrypt(source.credentials_encrypted)
    adapter = PlexAdapter(source.base_url, credentials["api_key"])
    libraries: list[ManagedLibrary] = []
    for item in adapter.discover_libraries():
        library = session.scalar(
            select(ManagedLibrary).where(
                ManagedLibrary.plex_integration_id == source.id,
                ManagedLibrary.external_id == item.external_id,
            )
        )
        if library is None:
            library = ManagedLibrary(
                plex_integration_id=source.id,
                external_id=item.external_id,
                name=item.name,
                media_type=item.media_type,
                enabled=False,
            )
            session.add(library)
        else:
            library.name = item.name
            library.media_type = item.media_type
        libraries.append(library)
    session.flush()
    return libraries


def change_management_mode(
    integration: IntegrationInstance, target: str, *, confirmed: bool
) -> tuple[str, str]:
    if integration.kind not in ARR_KINDS:
        raise ValueError("Management mode only applies to Arr integrations")
    normalized_target = target.strip().upper()
    if normalized_target not in MANAGEMENT_MODES:
        raise ValueError("Invalid management mode")
    previous = integration.management_mode or "PROTECTED"
    if _MODE_RANK[normalized_target] > _MODE_RANK[previous] and not confirmed:
        raise ValueError("Confirm more permissive management before applying this mode")
    integration.management_mode = normalized_target
    if normalized_target != "MANAGED":
        integration.active_management_enabled = False
    return previous, normalized_target


def set_active_management(integration: IntegrationInstance, *, enabled: bool) -> None:
    if integration.kind not in ACTIVE_MANAGEMENT_KINDS:
        raise ValueError("Active Management does not apply to this integration")
    if not enabled:
        integration.active_management_enabled = False
        return
    if not integration.enabled:
        raise ValueError("Enable the integration before Active Management")
    if integration.kind in ARR_KINDS and integration.management_mode != "MANAGED":
        raise ValueError("Set this Arr integration to Managed first")
    if integration.health_status != "HEALTHY" or integration.last_success_at is None:
        raise ValueError("A successful connection test is required")
    if integration.full_sync_completed_at is None:
        raise ValueError("A successful full inventory sync is required")
    if integration.dry_run_evaluated_at is None:
        raise ValueError("A current dry-run evaluation is required")
    integration.active_management_enabled = True
