from __future__ import annotations

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import quote_plus, urlencode

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.persistence.database import Database
from app.persistence.models import (
    AdminUser,
    DeletionJob,
    DryRunProposal,
    EventRecord,
    IntegrationInstance,
    IntegrationLibraryMapping,
    ManagedLibrary,
    ManualDeletionBatch,
    MediaFileRevision,
    MediaIdentity,
    MediaLifecycle,
    Playback,
    RequesterProfile,
    RequestRecord,
    SourceFreshness,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
    TrackerPolicy,
)
from app.presentation import format_local_date, format_local_datetime
from app.security.auth import (
    LoginRateLimiter,
    csrf_token,
    hash_password,
    verify_csrf,
    verify_password,
)
from app.security.credentials import CredentialCipher
from app.services.deletion_jobs import (
    DeletionBlocked,
    DeletionJobError,
    approve_movie_job,
    cancel_movie_job,
    create_movie_job,
    execute_movie_job,
    invalidate_queued_jobs,
    retry_movie_reconciliation,
)
from app.services.dry_run import (
    discovered_tracker_domains,
    evaluate_dry_run,
    normalize_tracker_domain,
    save_tracker_policy,
)
from app.services.events import append_event
from app.services.integrations import (
    change_management_mode,
    create_integration,
    discover_from_overseerr,
    discover_plex_libraries,
    remove_integration_local_data,
    set_active_management,
    set_plex_library_mapping,
    test_integration,
    update_integration,
)
from app.services.inventory import (
    get_inventory_policy,
    preview_integration,
    recompute_decisions,
    set_manual_protection,
    set_requester_protection,
    source_is_fresh,
    sync_integration,
)
from app.services.manual_management import (
    ManualManagementError,
    batch_results,
    build_manual_management_page,
    create_manual_batch,
    execute_manual_batch,
    resolve_manual_selection,
)
from app.services.media_workbench import WorkbenchRow, build_workbench_page
from app.services.rollout import change_rollout_mode, get_rollout_policy
from app.services.scheduler import inventory_scheduler_loop
from app.services.sync_coordinator import SyncAlreadyRunning, SyncCoordinator
from app.settings import Settings

APP_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_ROOT / "templates")


def _static_asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((APP_ROOT / "static").iterdir()):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


STATIC_ASSET_VERSION = _static_asset_version()


def _session(request: Request):
    yield from request.app.state.database.session()


def _remote_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _current_admin(request: Request, session: Session) -> AdminUser | None:
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    return session.scalar(
        select(AdminUser).where(AdminUser.id == admin_id, AdminUser.disabled.is_(False))
    )


def _require_admin(request: Request, session: Session) -> AdminUser:
    admin = _current_admin(request, session)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return admin


def _render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    timezone = request.app.state.settings.timezone
    values = {
        "request": request,
        "app_name": request.app.state.settings.app_name,
        "static_asset_version": STATIC_ASSET_VERSION,
        "csrf_token": csrf_token(request),
        "error": None,
        "errors": {},
        "username": "",
        "format_date": lambda value: format_local_date(value, timezone),
        "format_datetime": lambda value: format_local_datetime(value, timezone),
    }
    values.update(context or {})
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=values,
        status_code=status_code,
    )


def _integration_context(session: Session, sync_activity=None) -> dict:
    integrations = session.scalars(
        select(IntegrationInstance).order_by(IntegrationInstance.kind, IntegrationInstance.name)
    ).all()
    libraries = session.scalars(select(ManagedLibrary).order_by(ManagedLibrary.name)).all()
    libraries_by_plex: dict[str, list[ManagedLibrary]] = {}
    for library in libraries:
        libraries_by_plex.setdefault(library.plex_integration_id, []).append(library)
    library_mappings = {
        mapping.integration_id: mapping
        for mapping in session.scalars(select(IntegrationLibraryMapping)).all()
    }
    enabled_libraries_by_arr: dict[str, list[ManagedLibrary]] = {}
    for integration in integrations:
        if integration.kind not in ("RADARR", "SONARR"):
            continue
        required_type = "movie" if integration.kind == "RADARR" else "show"
        enabled_libraries_by_arr[integration.id] = [
            library
            for library in libraries
            if library.enabled and library.media_type == required_type
        ]
    freshness_by_integration = {
        row.integration_id: row for row in session.scalars(select(SourceFreshness)).all()
    }
    requesters_by_integration: dict[str, list[RequesterProfile]] = {}
    requesters = session.scalars(
        select(RequesterProfile).order_by(
            RequesterProfile.display_name,
            RequesterProfile.username,
            RequesterProfile.email,
            RequesterProfile.external_id,
        )
    ).all()
    for profile in requesters:
        requesters_by_integration.setdefault(profile.integration_id, []).append(profile)
    tracker_policies = {
        policy.normalized_domain: policy
        for policy in session.scalars(select(TrackerPolicy)).all()
    }
    discovered_domains = dict(discovered_tracker_domains(session))
    available_tracker_domains = [
        {
            "domain": domain,
            "torrent_count": discovered_domains.get(domain, 0),
            "policy": tracker_policies.get(domain),
            "selected": bool(tracker_policies.get(domain) and tracker_policies[domain].selected),
        }
        for domain in sorted(set(discovered_domains) | set(tracker_policies))
    ]
    tracker_domains = [tracker for tracker in available_tracker_domains if tracker["selected"]]
    return {
        "integrations": integrations,
        "libraries_by_plex": libraries_by_plex,
        "library_mappings": library_mappings,
        "enabled_libraries_by_arr": enabled_libraries_by_arr,
        "freshness_by_integration": freshness_by_integration,
        "requesters_by_integration": requesters_by_integration,
        "sync_activity": sync_activity,
        "source_is_fresh": source_is_fresh,
        "policy": get_inventory_policy(session),
        "tracker_domains": tracker_domains,
        "available_tracker_domains": available_tracker_domains,
        "rollout_policy": get_rollout_policy(session),
    }


DRY_RUN_STATES = ("ELIGIBLE", "BLOCKED", "RETAINED", "PROTECTED")
DRY_RUN_INVALIDATING_EVENTS = (
    "inventory.sync_completed",
    "inventory.scheduled_sync_completed",
    "inventory.policy_changed",
    "tracker.policy_changed",
    "tracker.selection_changed",
    "requester.protection_changed",
    "media.manual_protection_changed",
    "media.manual_protection_batch_changed",
    "plex.library_selection_changed",
    "integration.management_mode_changed",
    "integration.removed",
)


def _deletion_queue_context(session: Session, requested_state: str = "") -> dict:
    active_state = requested_state.upper() if requested_state.upper() in DRY_RUN_STATES else ""
    dry_run_counts = {
        state: int(count)
        for state, count in session.execute(
            select(DryRunProposal.state, func.count())
            .group_by(DryRunProposal.state)
        )
    }
    dry_run_eligible_bytes = int(
        session.scalar(
            select(func.coalesce(func.sum(DryRunProposal.estimated_bytes), 0)).where(
                DryRunProposal.state == "ELIGIBLE"
            )
        )
        or 0
    )
    proposal_evaluated_at = session.scalar(select(func.max(DryRunProposal.evaluated_at)))
    evaluation_event_at = session.scalar(
        select(func.max(EventRecord.occurred_at)).where(
            EventRecord.event_type == "dry_run.evaluated"
        )
    )
    evaluation_times = [value for value in (proposal_evaluated_at, evaluation_event_at) if value]
    dry_run_evaluated_at = max(evaluation_times) if evaluation_times else None
    last_invalidating_change = session.scalar(
        select(func.max(EventRecord.occurred_at)).where(
            EventRecord.event_type.in_(DRY_RUN_INVALIDATING_EVENTS)
        )
    )
    preview_needs_recalculation = dry_run_evaluated_at is None or bool(
        last_invalidating_change and last_invalidating_change > dry_run_evaluated_at
    )
    rows_query = (
        select(DryRunProposal, MediaLifecycle, MediaIdentity, IntegrationInstance)
        .join(MediaLifecycle, MediaLifecycle.id == DryRunProposal.lifecycle_id)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .join(IntegrationInstance, IntegrationInstance.id == MediaLifecycle.integration_id)
        .order_by(
            case(
                (DryRunProposal.state == "ELIGIBLE", 0),
                (DryRunProposal.state == "BLOCKED", 1),
                (DryRunProposal.state == "RETAINED", 2),
                else_=3,
            ),
            MediaIdentity.canonical_title,
            MediaIdentity.season_number,
        )
    )
    count_query = select(func.count()).select_from(DryRunProposal)
    if active_state:
        rows_query = rows_query.where(DryRunProposal.state == active_state)
        count_query = count_query.where(DryRunProposal.state == active_state)
    dry_run_rows = session.execute(rows_query.limit(30)).all()
    filtered_result_count = int(session.scalar(count_query) or 0)
    deletion_jobs = session.execute(
        select(DeletionJob, MediaLifecycle, MediaIdentity, IntegrationInstance)
        .join(MediaLifecycle, MediaLifecycle.id == DeletionJob.lifecycle_id)
        .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
        .join(IntegrationInstance, IntegrationInstance.id == MediaLifecycle.integration_id)
        .order_by(DeletionJob.created_at.desc())
        .limit(30)
    ).all()
    return {
        "dry_run_counts": dry_run_counts,
        "dry_run_eligible_bytes": dry_run_eligible_bytes,
        "dry_run_evaluated_at": dry_run_evaluated_at,
        "dry_run_rows": dry_run_rows,
        "dry_run_states": DRY_RUN_STATES,
        "active_dry_run_state": active_state,
        "filtered_result_count": filtered_result_count,
        "preview_needs_recalculation": preview_needs_recalculation,
        "rollout_policy": get_rollout_policy(session),
        "deletion_jobs": deletion_jobs,
        "jobs_by_lifecycle": {job.lifecycle_id: job for job, *_rest in deletion_jobs},
    }


def _integrations_redirect(
    *,
    message: str | None = None,
    error: str | None = None,
    warning: str | None = None,
):
    if error:
        return RedirectResponse(
            f"/settings?error={quote_plus(error)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if warning:
        return RedirectResponse(
            f"/settings?warning={quote_plus(warning)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        f"/settings?message={quote_plus(message or '')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _media_redirect(
    *,
    message: str | None = None,
    error: str | None = None,
    query: dict[str, str] | None = None,
):
    key = "error" if error else "message"
    value = error or message or ""
    params = {name: item for name, item in (query or {}).items() if item}
    params[key] = value
    return RedirectResponse(
        f"/media?{urlencode(params)}", status_code=status.HTTP_303_SEE_OTHER
    )


def _deletion_redirect(
    *,
    job_id: str | None = None,
    message: str | None = None,
    error: str | None = None,
    warning: str | None = None,
):
    params: dict[str, str] = {}
    if error:
        params["error"] = error
    elif warning:
        params["warning"] = warning
    elif message:
        params["message"] = message
    path = f"/deletion-jobs/{job_id}" if job_id else "/deletion-queue"
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"{path}{query}", status_code=status.HTTP_303_SEE_OTHER)


def _manual_management_redirect(
    *,
    requester_profile_id: str = "",
    integration_id: str = "",
    tracker_filter: str = "ALL",
    watch_filter: str = "ALL",
    sort_field: str = "NAME",
    sort_direction: str = "ASC",
    batch_id: str = "",
    message: str | None = None,
    error: str | None = None,
):
    query: dict[str, str] = {
        "requester": requester_profile_id,
        "instance": integration_id,
        "tracker": tracker_filter,
        "watch": watch_filter,
        "sort": sort_field,
        "direction": sort_direction,
    }
    if batch_id:
        query["batch"] = batch_id
    if message:
        query["message"] = message
    if error:
        query["error"] = error
    return RedirectResponse(
        f"/manual-management?{urlencode(query)}", status_code=status.HTTP_303_SEE_OTHER
    )


def _media_lifecycle_query(
    *,
    search: str = "",
    source: str = "",
    media_type: str = "",
    library_state: str = "",
    decision: str = "",
    watch_state: str = "",
):
    query = (
        select(MediaLifecycle, MediaIdentity, IntegrationInstance)
        .join(MediaIdentity, MediaLifecycle.identity_id == MediaIdentity.id)
        .join(
            IntegrationInstance,
            MediaLifecycle.integration_id == IntegrationInstance.id,
        )
    )
    normalized_search = search.strip()[:120]
    if normalized_search:
        query = query.where(
            MediaIdentity.canonical_title.ilike(f"%{normalized_search}%")
        )
    if source:
        query = query.where(IntegrationInstance.id == source)
    if media_type in {"MOVIE", "SEASON"}:
        query = query.where(MediaIdentity.media_type == media_type)
    if library_state in {"ACTIVE", "MISSING"}:
        query = query.where(MediaLifecycle.state == library_state)
    if decision in {
        "KEEP_PROTECTED",
        "KEEP_RETAINED",
        "REVIEW_ELIGIBLE",
        "BLOCKED_STALE",
        "BLOCKED_UNKNOWN",
    }:
        query = query.where(
            MediaLifecycle.decision == decision,
            MediaLifecycle.state == "ACTIVE",
        )
    if watch_state == "WATCHED":
        query = query.where(
            MediaLifecycle.watched.is_(True), MediaLifecycle.state == "ACTIVE"
        )
    elif watch_state == "NEVER_WATCHED":
        query = query.where(
            MediaLifecycle.watched.is_(False), MediaLifecycle.state == "ACTIVE"
        )
    return query, normalized_search


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings()
    active_settings.prepare()
    database = Database(active_settings.sqlalchemy_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        scheduler_task = None
        if active_settings.app_environment != "test":
            scheduler_task = asyncio.create_task(
                inventory_scheduler_loop(
                    application.state.database,
                    application.state.credential_cipher,
                    poll_seconds=active_settings.scheduler_poll_seconds,
                    coordinator=application.state.sync_coordinator,
                )
            )
        yield
        if scheduler_task:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task

    app = FastAPI(
        title=active_settings.app_name,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.state.database = database
    app.state.credential_cipher = CredentialCipher(active_settings.credential_encryption_key)
    app.state.sync_coordinator = SyncCoordinator()
    app.state.login_limiter = LoginRateLimiter(
        active_settings.login_attempt_limit,
        active_settings.login_attempt_window_seconds,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=active_settings.signing_secret,
        session_cookie="rpm_session",
        max_age=active_settings.session_max_age_seconds,
        same_site="lax",
        https_only=active_settings.secure_cookies,
    )
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        )
        return response

    @app.exception_handler(status.HTTP_401_UNAUTHORIZED)
    async def unauthenticated(_request: Request, _exception: HTTPException):
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/health")
    def health(request: Request):
        ready = request.app.state.database.ready()
        return JSONResponse(
            {"status": "ok" if ready else "unavailable", "database": ready},
            status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/", include_in_schema=False)
    def root(request: Request, session: Session = Depends(_session)):
        if session.scalar(select(func.count()).select_from(AdminUser)) == 0:
            return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
        if _current_admin(request, session):
            return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request, session: Session = Depends(_session)):
        if session.scalar(select(func.count()).select_from(AdminUser)) > 0:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        return _render(request, "setup.html")

    @app.post("/setup", response_class=HTMLResponse)
    def setup_submit(
        request: Request,
        username: str = Form(),
        password: str = Form(),
        password_confirm: str = Form(),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        if session.scalar(select(func.count()).select_from(AdminUser)) > 0:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Setup is complete")

        normalized_username = username.strip()
        errors: dict[str, str] = {}
        if len(normalized_username) < 3:
            errors["username"] = "Username must be at least 3 characters."
        if len(password) < 8:
            errors["password"] = "Password must be at least 8 characters."
        if password != password_confirm:
            errors["password_confirm"] = "Passwords do not match."
        if errors:
            return _render(
                request,
                "setup.html",
                {"errors": errors, "username": normalized_username},
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )

        admin = AdminUser(username=normalized_username, password_hash=hash_password(password))
        session.add(admin)
        session.flush()
        append_event(
            session,
            event_type="admin.created",
            entity_type="admin_user",
            entity_id=admin.id,
            actor_type="system",
            actor_id=None,
            payload={"username": admin.username, "source": "setup"},
        )
        session.commit()
        request.session.clear()
        request.session["admin_id"] = admin.id
        csrf_token(request)
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, session: Session = Depends(_session)):
        if _current_admin(request, session):
            return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)
        return _render(request, "login.html")

    @app.post("/login", response_class=HTMLResponse)
    def login_submit(
        request: Request,
        username: str = Form(),
        password: str = Form(),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        limiter: LoginRateLimiter = request.app.state.login_limiter
        remote_key = _remote_key(request)
        if not limiter.allowed(remote_key):
            return _render(
                request,
                "login.html",
                {"error": "Too many attempts. Try again later.", "username": username},
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        admin = session.scalar(select(AdminUser).where(AdminUser.username == username.strip()))
        if admin is None or admin.disabled or not verify_password(admin.password_hash, password):
            limiter.fail(remote_key)
            return _render(
                request,
                "login.html",
                {"error": "Invalid username or password.", "username": username.strip()},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        limiter.succeed(remote_key)
        append_event(
            session,
            event_type="admin.login_succeeded",
            entity_type="admin_user",
            entity_id=admin.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"remote_address": remote_key},
        )
        session.commit()
        request.session.clear()
        request.session["admin_id"] = admin.id
        csrf_token(request)
        return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/logout")
    def logout(
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        append_event(
            session,
            event_type="admin.logout",
            entity_type="admin_user",
            entity_id=admin.id,
            actor_type="admin",
            actor_id=admin.id,
        )
        session.commit()
        request.session.clear()
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, session: Session = Depends(_session)):
        admin = _require_admin(request, session)
        event_count = session.scalar(select(func.count()).select_from(EventRecord)) or 0
        return _render(
            request,
            "dashboard.html",
            {
                "admin": admin,
                "event_count": event_count,
                "rollout_mode": get_rollout_policy(session).mode,
            },
        )

    @app.get("/integrations", response_class=HTMLResponse)
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(
        request: Request,
        message: str | None = None,
        error: str | None = None,
        warning: str | None = None,
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        context = _integration_context(session, request.app.state.sync_coordinator.current())
        context.update(
            {"admin": admin, "message": message, "error": error, "warning": warning}
        )
        return _render(
            request,
            "integrations.html",
            context,
        )

    @app.get("/deletion-queue", response_class=HTMLResponse)
    def deletion_queue_page(
        request: Request,
        message: str | None = None,
        error: str | None = None,
        warning: str | None = None,
        state: str = Query(""),
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        context = _deletion_queue_context(session, state)
        context.update(
            {"admin": admin, "message": message, "error": error, "warning": warning}
        )
        return _render(request, "deletion_queue.html", context)

    @app.get("/manual-management", response_class=HTMLResponse)
    def manual_management_page(
        request: Request,
        requester: str = Query(""),
        instance: str = Query(""),
        tracker: str = Query("ALL"),
        watch: str = Query("ALL"),
        sort: str = Query("NAME"),
        direction: str = Query("ASC"),
        page: int = Query(1, ge=1),
        batch: str = Query(""),
        message: str | None = None,
        error: str | None = None,
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        context = build_manual_management_page(
            session,
            requester_profile_id=requester,
            integration_id=instance,
            tracker_filter=tracker,
            watch_filter=watch,
            sort_field=sort,
            sort_direction=direction,
            page=page,
        )
        result = batch_results(session, batch) if batch else None
        return _render(
            request,
            "manual_management.html",
            {
                "admin": admin,
                "manual_page": context,
                "batch_result": result,
                "message": message,
                "error": error,
                "sync_activity": request.app.state.sync_coordinator.current(),
                "rollout_policy": get_rollout_policy(session),
            },
        )

    @app.post("/manual-management/execute")
    def manual_management_execute(
        request: Request,
        requester_profile_id: str = Form(),
        integration_id: str = Form(),
        tracker_filter: str = Form("ALL"),
        watch_filter: str = Form("ALL"),
        sort_field: str = Form("NAME"),
        sort_direction: str = Form("ASC"),
        lifecycle_ids: list[str] = Form(default=[]),
        excluded_lifecycle_ids: list[str] = Form(default=[]),
        select_all_filtered: str | None = Form(None),
        add_import_exclusion: str | None = Form(None),
        acknowledge: str | None = Form(None),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        if acknowledge != "yes":
            return _manual_management_redirect(
                requester_profile_id=requester_profile_id,
                integration_id=integration_id,
                tracker_filter=tracker_filter,
                watch_filter=watch_filter,
                sort_field=sort_field,
                sort_direction=sort_direction,
                error="Confirm the selected library-file deletions before continuing.",
            )
        batch: ManualDeletionBatch | None = None
        try:
            with request.app.state.sync_coordinator.acquire(
                f"manual:{uuid.uuid4()}",
                "Manual Management",
                trigger="manual_management",
            ):
                profile, integration, candidates = resolve_manual_selection(
                    session,
                    requester_profile_id=requester_profile_id,
                    integration_id=integration_id,
                    tracker_filter=tracker_filter,
                    watch_filter=watch_filter,
                    lifecycle_ids=lifecycle_ids,
                    excluded_lifecycle_ids=excluded_lifecycle_ids,
                    select_all_filtered=select_all_filtered == "yes",
                )
                batch = create_manual_batch(
                    session,
                    profile=profile,
                    integration=integration,
                    candidates=candidates,
                    admin_id=admin.id,
                    add_import_exclusion=add_import_exclusion == "yes",
                )
                execute_manual_batch(
                    session,
                    batch,
                    request.app.state.credential_cipher,
                    admin_id=admin.id,
                )
        except SyncAlreadyRunning:
            session.rollback()
            return _manual_management_redirect(
                requester_profile_id=requester_profile_id,
                integration_id=integration_id,
                tracker_filter=tracker_filter,
                watch_filter=watch_filter,
                sort_field=sort_field,
                sort_direction=sort_direction,
                error=(
                    "Another inventory or deletion operation is running. "
                    "Try again after it finishes."
                ),
            )
        except OperationalError as database_error:
            retry_batch_id = batch.id if batch else ""
            session.rollback()
            if "database is locked" not in str(database_error).lower():
                raise
            return _manual_management_redirect(
                requester_profile_id=requester_profile_id,
                integration_id=integration_id,
                tracker_filter=tracker_filter,
                watch_filter=watch_filter,
                sort_field=sort_field,
                sort_direction=sort_direction,
                batch_id=retry_batch_id,
                error=(
                    "The database is busy with another operation. "
                    "Try Manual Management again after it finishes."
                ),
            )
        except ManualManagementError as validation_error:
            session.rollback()
            return _manual_management_redirect(
                requester_profile_id=requester_profile_id,
                integration_id=integration_id,
                tracker_filter=tracker_filter,
                watch_filter=watch_filter,
                sort_field=sort_field,
                sort_direction=sort_direction,
                error=str(validation_error),
            )
        assert batch is not None
        summary = f"Manual run finished: {batch.completed_items} completed"
        if batch.failed_items:
            summary += f" and {batch.failed_items} need attention"
        return _manual_management_redirect(
            requester_profile_id=requester_profile_id,
            integration_id=integration_id,
            tracker_filter=tracker_filter,
            watch_filter=watch_filter,
            sort_field=sort_field,
            sort_direction=sort_direction,
            batch_id=batch.id,
            message=f"{summary}.",
        )

    @app.post("/manual-management/batches/{batch_id}/retry")
    def manual_management_retry(
        batch_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        batch = session.get(ManualDeletionBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            with request.app.state.sync_coordinator.acquire(
                batch.id,
                "Manual Management retry",
                trigger="manual_management_retry",
            ):
                execute_manual_batch(
                    session,
                    batch,
                    request.app.state.credential_cipher,
                    admin_id=admin.id,
                )
        except SyncAlreadyRunning:
            session.rollback()
            return _manual_management_redirect(
                batch_id=batch.id,
                error="Another inventory or deletion operation is running. Try again later.",
            )
        except OperationalError as database_error:
            session.rollback()
            if "database is locked" not in str(database_error).lower():
                raise
            return _manual_management_redirect(
                batch_id=batch.id,
                error=(
                    "The database is busy with another operation. "
                    "Retry unfinished steps after it finishes."
                ),
            )
        except ManualManagementError as execution_error:
            return _manual_management_redirect(batch_id=batch.id, error=str(execution_error))
        return _manual_management_redirect(
            requester_profile_id=batch.requester_profile_id,
            integration_id=batch.integration_id,
            batch_id=batch.id,
            message=(
                f"Retry finished: {batch.completed_items} completed and "
                f"{batch.failed_items} need attention."
            ),
        )

    @app.post("/integrations")
    def integrations_create(
        request: Request,
        kind: str = Form(),
        name: str = Form(),
        base_url: str = Form(),
        api_key: str = Form(""),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        try:
            integration = create_integration(
                session,
                request.app.state.credential_cipher,
                kind=kind,
                name=name,
                base_url=base_url,
                api_key=api_key,
            )
        except ValueError as validation_error:
            validation_message = str(validation_error)
            field = "kind"
            if "URL" in validation_message or "url" in validation_message:
                field = "base_url"
            elif "Name" in validation_message:
                field = "name"
            elif "API key" in validation_message or "token" in validation_message:
                field = "api_key"
            context = _integration_context(session)
            context.update(
                {
                    "admin": admin,
                    "errors": {field: validation_message},
                    "form": {
                        "kind": kind,
                        "name": name,
                        "base_url": base_url,
                    },
                }
            )
            return _render(
                request,
                "integrations.html",
                context,
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        append_event(
            session,
            event_type="integration.configured",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"kind": integration.kind, "base_url": integration.base_url},
        )
        session.commit()
        return _integrations_redirect(message="Integration saved disabled by default.")

    @app.post("/integrations/{integration_id}/test")
    def integrations_test(
        integration_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        result = test_integration(integration, request.app.state.credential_cipher)
        append_event(
            session,
            event_type="integration.tested",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"healthy": result.healthy, "version": result.version},
        )
        session.commit()
        if result.healthy:
            detail = f"Connection succeeded{f' · {result.version}' if result.version else ''}."
            return _integrations_redirect(message=detail)
        return _integrations_redirect(
            error="Connection failed. Check the saved URL and credentials."
        )

    @app.get("/integrations/{integration_id}/edit", response_class=HTMLResponse)
    def integrations_edit_page(
        integration_id: str,
        request: Request,
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return _render(
            request,
            "integration_edit.html",
            {
                "admin": admin,
                "integration": integration,
                "form": {
                    "name": integration.name,
                    "base_url": integration.base_url,
                },
                "delete_error": None,
            },
        )

    @app.post("/integrations/{integration_id}/edit")
    def integrations_edit(
        integration_id: str,
        request: Request,
        name: str = Form(),
        base_url: str = Form(),
        api_key: str = Form(""),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            credentials_replaced = update_integration(
                session,
                request.app.state.credential_cipher,
                integration,
                name=name,
                base_url=base_url,
                api_key=api_key,
            )
        except ValueError as validation_error:
            validation_message = str(validation_error)
            field = "api_key"
            if "URL" in validation_message or "url" in validation_message:
                field = "base_url"
            elif "Name" in validation_message:
                field = "name"
            return _render(
                request,
                "integration_edit.html",
                {
                    "admin": admin,
                    "integration": integration,
                    "errors": {field: validation_message},
                    "form": {
                        "name": name,
                        "base_url": base_url,
                    },
                    "delete_error": None,
                },
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        append_event(
            session,
            event_type="integration.updated",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "kind": integration.kind,
                "base_url": integration.base_url,
                "credentials_replaced": credentials_replaced,
                "enabled": integration.enabled,
            },
        )
        session.commit()
        return _integrations_redirect(
            message=f"{integration.name} updated. Test the connection before enabling it."
        )

    @app.post("/integrations/{integration_id}/delete")
    def integrations_delete(
        integration_id: str,
        request: Request,
        confirm_name: str = Form(),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if confirm_name.strip() != integration.name:
            return _render(
                request,
                "integration_edit.html",
                {
                    "admin": admin,
                    "integration": integration,
                    "form": {
                        "name": integration.name,
                        "base_url": integration.base_url,
                        "username": "",
                    },
                    "delete_error": f"Type {integration.name} exactly to confirm removal.",
                },
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        related_job = session.scalar(
            select(DeletionJob)
            .join(MediaLifecycle, MediaLifecycle.id == DeletionJob.lifecycle_id)
            .where(MediaLifecycle.integration_id == integration.id)
        )
        any_in_flight_job = session.scalar(
            select(DeletionJob).where(
                DeletionJob.state.not_in(("COMPLETED", "CANCELLED"))
            )
        )
        if related_job is not None or (
            integration.kind in {"QBITTORRENT", "PLEX", "OVERSEERR"}
            and any_in_flight_job is not None
        ):
            return _integrations_redirect(
                error=(
                    f"{integration.name} is referenced by a deletion audit or active approval "
                    "and cannot be removed. Disable it instead."
                )
            )
        removed_name = integration.name
        append_event(
            session,
            event_type="integration.removed",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "kind": integration.kind,
                "base_url": integration.base_url,
                "scope": "local_configuration_and_inventory_only",
            },
        )
        remove_integration_local_data(session, integration)
        session.commit()
        return _integrations_redirect(
            message=f"{removed_name} removed from this app. No external service was changed."
        )

    @app.post("/integrations/{integration_id}/enabled")
    def integrations_enabled(
        integration_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        integration.enabled = not integration.enabled
        if not integration.enabled:
            integration.active_management_enabled = False
            invalidate_queued_jobs(
                session, reason="INTEGRATION_DISABLED"
            )
        append_event(
            session,
            event_type="integration.enabled_changed",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "enabled": integration.enabled,
                "active_management_enabled": False,
            },
        )
        session.commit()
        state = "enabled read-only" if integration.enabled else "disabled"
        return _integrations_redirect(message=f"{integration.name} {state}.")

    @app.post("/integrations/{integration_id}/discover")
    def integrations_discover(
        integration_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        source = session.get(IntegrationInstance, integration_id)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if not source.enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Enable Overseerr before discovery",
            )
        try:
            discovered = discover_from_overseerr(
                session, source, request.app.state.credential_cipher
            )
        except (httpx.HTTPError, ValueError, KeyError, OSError) as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Overseerr discovery failed",
            ) from error
        append_event(
            session,
            event_type="integration.discovery_completed",
            entity_type="integration",
            entity_id=source.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"discovered_count": len(discovered)},
        )
        session.commit()
        return _integrations_redirect(
            message=f"Discovery finished. Found {len(discovered)} Arr instances."
        )

    @app.post("/integrations/{integration_id}/libraries/discover")
    def plex_libraries_discover(
        integration_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        source = session.get(IntegrationInstance, integration_id)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            libraries = discover_plex_libraries(
                session, source, request.app.state.credential_cipher
            )
        except (httpx.HTTPError, ValueError, KeyError) as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
        append_event(
            session,
            event_type="plex.libraries_discovered",
            entity_type="integration",
            entity_id=source.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"discovered_count": len(libraries)},
        )
        session.commit()
        return _integrations_redirect(
            message=f"Found {len(libraries)} Plex libraries. Select only libraries in scope."
        )

    @app.post("/libraries/{library_id}/enabled")
    def library_enabled(
        library_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        library = session.get(ManagedLibrary, library_id)
        if library is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        plex = session.get(IntegrationInstance, library.plex_integration_id)
        if plex is None or not plex.enabled:
            return _integrations_redirect(error="Enable Plex before selecting libraries.")
        library.enabled = not library.enabled
        if not library.enabled:
            mapping = session.scalar(
                select(IntegrationLibraryMapping).where(
                    IntegrationLibraryMapping.library_id == library.id
                )
            )
            if mapping is not None:
                for lifecycle in session.scalars(
                    select(MediaLifecycle).where(
                        MediaLifecycle.integration_id == mapping.integration_id
                    )
                ).all():
                    lifecycle.library_id = None
                    lifecycle.plex_rating_key = None
                session.delete(mapping)
        append_event(
            session,
            event_type="plex.library_selection_changed",
            entity_type="managed_library",
            entity_id=library.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "plex_integration_id": library.plex_integration_id,
                "external_id": library.external_id,
                "enabled": library.enabled,
            },
        )
        session.commit()
        return _integrations_redirect(
            message=f"{library.name} {'selected' if library.enabled else 'removed from scope'}."
        )

    @app.post("/integrations/{integration_id}/plex-library")
    def integration_plex_library(
        integration_id: str,
        request: Request,
        library_id: str = Form(""),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            mapping = set_plex_library_mapping(
                session,
                integration,
                library_id.strip() or None,
            )
        except ValueError as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
        append_event(
            session,
            event_type="integration.plex_library_mapping_changed",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "library_id": mapping.library_id if mapping else None,
                "source": mapping.source if mapping else "AUTO_PENDING",
            },
        )
        session.commit()
        if mapping is None:
            return _integrations_redirect(
                message=(
                    f"{integration.name} will detect its Plex library during the next Plex sync."
                )
            )
        library = session.get(ManagedLibrary, mapping.library_id)
        return _integrations_redirect(
            message=f"{integration.name} is paired with {library.name if library else 'Plex'}."
        )

    @app.post("/integrations/{integration_id}/management-mode")
    def integration_management_mode(
        integration_id: str,
        request: Request,
        management_mode: str = Form(),
        confirm_permissive: str | None = Form(None),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            previous, current = change_management_mode(
                integration,
                management_mode,
                confirmed=confirm_permissive == "yes",
            )
        except ValueError as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
        if current != "MANAGED":
            invalidate_queued_jobs(session, reason="MANAGEMENT_MODE_REDUCED")
        append_event(
            session,
            event_type="integration.management_mode_changed",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"previous": previous, "current": current},
        )
        session.commit()
        return _integrations_redirect(message=f"{integration.name} is now {current.title()}.")

    @app.post("/integrations/{integration_id}/active-management")
    def integration_active_management(
        integration_id: str,
        request: Request,
        confirm_active: str | None = Form(None),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        target = not integration.active_management_enabled
        if target:
            if get_rollout_policy(session).mode != "APPROVAL_REQUIRED":
                return _integrations_redirect(
                    error="Active Management requires the Approval Required rollout mode."
                )
            if confirm_active != "yes":
                return _integrations_redirect(
                    error="Confirm Active Management before enabling it."
                )
        try:
            set_active_management(integration, enabled=target)
        except ValueError as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
        if not target:
            invalidate_queued_jobs(session, reason="ACTIVE_MANAGEMENT_DISABLED")
        append_event(
            session,
            event_type="integration.active_management_changed",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"active_management_enabled": target},
        )
        session.commit()
        state = "enabled" if target else "disabled"
        return _integrations_redirect(message=f"Active Management {state} for {integration.name}.")

    @app.post("/integrations/{integration_id}/sync")
    def integration_sync(
        integration_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            with request.app.state.sync_coordinator.acquire(
                integration.id, integration.name, trigger="manual"
            ):
                run = sync_integration(session, integration, request.app.state.credential_cipher)
                append_event(
                    session,
                    event_type="inventory.sync_completed",
                    entity_type="integration",
                    entity_id=integration.id,
                    actor_type="admin",
                    actor_id=admin.id,
                    payload={"status": run.status, "counts": run.counts},
                )
                session.commit()
        except SyncAlreadyRunning as conflict:
            session.rollback()
            return _integrations_redirect(
                warning=(
                    f"Sync running: {conflict.activity.integration_name}. "
                    "Try again after it finishes."
                )
            )
        except ValueError as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
        if run.status == "FAILED":
            return _integrations_redirect(
                error=f"{integration.name} sync failed: {run.sanitized_error}"
            )
        summary = ", ".join(f"{value} {key}" for key, value in run.counts.items())
        return _integrations_redirect(message=f"{integration.name} synchronized: {summary}.")

    @app.post("/requesters/{profile_id}/protected")
    def requester_protection_toggle(
        profile_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        profile = session.get(RequesterProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        target = not profile.protected
        set_requester_protection(session, profile, protected=target)
        append_event(
            session,
            event_type="requester.protection_changed",
            entity_type="requester_profile",
            entity_id=profile.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "integration_id": profile.integration_id,
                "external_id": profile.external_id,
                "protected": target,
            },
        )
        session.commit()
        label = profile.display_name or profile.username or profile.email or profile.external_id
        state = "enabled" if target else "removed"
        return _integrations_redirect(
            message=f"Requester protection {state} for {label}."
        )

    @app.post("/integrations/{integration_id}/preview")
    def integration_preview(
        integration_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        integration = session.get(IntegrationInstance, integration_id)
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            counts = preview_integration(session, integration, request.app.state.credential_cipher)
        except (httpx.HTTPError, ValueError, KeyError) as error:
            return _integrations_redirect(error=f"Preview failed: {error}")
        append_event(
            session,
            event_type="inventory.preview_completed",
            entity_type="integration",
            entity_id=integration.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"counts": counts},
        )
        session.commit()
        summary = ", ".join(f"{value} {key}" for key, value in counts.items())
        return _integrations_redirect(
            message=f"{integration.name} preview: {summary}. Nothing was saved."
        )

    @app.get("/media", response_class=HTMLResponse)
    def media_page(
        request: Request,
        message: str | None = None,
        error: str | None = None,
        search: str = Query(default="", max_length=120),
        source: str = "",
        media_type: str = "",
        library_state: str = "",
        decision: str = "",
        watch_state: str = "",
        sort: str = "retention_soonest",
        page: int = Query(default=1, ge=1),
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        lifecycle_query, normalized_search = _media_lifecycle_query(
            search=search,
            source=source,
            media_type=media_type,
            library_state=library_state,
            decision=decision,
            watch_state=watch_state,
        )
        records = session.execute(lifecycle_query).all()
        mapping_counts = dict(
            session.execute(
                select(
                    TorrentMediaMapping.lifecycle_id,
                    func.count(TorrentMediaMapping.id),
                ).group_by(TorrentMediaMapping.lifecycle_id)
            ).all()
        )
        rows = [
            WorkbenchRow(
                lifecycle=lifecycle,
                identity=identity,
                integration=integration,
                torrent_count=mapping_counts.get(lifecycle.id, 0),
            )
            for lifecycle, identity, integration in records
        ]
        valid_sorts = {"retention_soonest", "retention_latest", "title", "imported_newest"}
        normalized_sort = sort if sort in valid_sorts else "retention_soonest"
        workbench = build_workbench_page(rows, sort=normalized_sort, page=page)
        source_options = session.scalars(
            select(IntegrationInstance)
            .where(IntegrationInstance.kind.in_(("RADARR", "SONARR")))
            .order_by(IntegrationInstance.name)
        ).all()
        freshness = session.scalars(
            select(SourceFreshness).order_by(SourceFreshness.source_kind)
        ).all()
        fresh_count = sum(source_is_fresh(item) for item in freshness)
        return _render(
            request,
            "media.html",
            {
                "admin": admin,
                "workbench": workbench,
                "source_options": source_options,
                "filters": {
                    "search": normalized_search,
                    "source": source,
                    "media_type": media_type,
                    "library_state": library_state,
                    "decision": decision,
                    "watch_state": watch_state,
                    "sort": normalized_sort,
                },
                "active_filter_count": sum(
                    bool(value)
                    for value in (
                        normalized_search,
                        source,
                        media_type,
                        library_state,
                        decision,
                        watch_state,
                    )
                ),
                "previous_url": str(request.url.include_query_params(page=workbench.page - 1))
                if workbench.page > 1
                else None,
                "next_url": str(request.url.include_query_params(page=workbench.page + 1))
                if workbench.page < workbench.page_count
                else None,
                "freshness": freshness,
                "fresh_count": fresh_count,
                "message": message,
                "error": error,
                "source_is_fresh": source_is_fresh,
            },
        )

    @app.post("/media/protection")
    def media_protection_update(
        request: Request,
        operation: str = Form(),
        lifecycle_ids: list[str] = Form(default=[]),
        select_all_filtered: str = Form(""),
        search: str = Form(""),
        source: str = Form(""),
        media_type: str = Form(""),
        library_state: str = Form(""),
        decision: str = Form(""),
        watch_state: str = Form(""),
        sort: str = Form("retention_soonest"),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        valid_sorts = {"retention_soonest", "retention_latest", "title", "imported_newest"}
        normalized_sort = sort if sort in valid_sorts else "retention_soonest"
        filter_query = {
            "search": search.strip()[:120],
            "source": source,
            "media_type": media_type,
            "library_state": library_state,
            "decision": decision,
            "watch_state": watch_state,
            "sort": normalized_sort,
        }
        if operation not in {"protect", "unprotect"}:
            return _media_redirect(
                error="Choose whether to apply or remove manual protection.",
                query=filter_query,
            )

        selection_scope = "filtered" if select_all_filtered == "yes" else "selected"
        if selection_scope == "filtered":
            lifecycle_query, _normalized_search = _media_lifecycle_query(
                search=search,
                source=source,
                media_type=media_type,
                library_state=library_state,
                decision=decision,
                watch_state=watch_state,
            )
            lifecycles = [record[0] for record in session.execute(lifecycle_query).all()]
        else:
            selected_ids = list(dict.fromkeys(lifecycle_ids))
            lifecycles = (
                session.scalars(
                    select(MediaLifecycle).where(MediaLifecycle.id.in_(selected_ids))
                ).all()
                if selected_ids
                else []
            )
        if not lifecycles:
            return _media_redirect(
                error="Select at least one lifecycle or choose all filtered results.",
                query=filter_query,
            )

        target = operation == "protect"
        changed = set_manual_protection(session, lifecycles, protected=target)
        correlation_id = str(uuid.uuid4())
        for lifecycle in changed:
            append_event(
                session,
                event_type="media.manual_protection_changed",
                entity_type="media_lifecycle",
                entity_id=lifecycle.id,
                actor_type="admin",
                actor_id=admin.id,
                correlation_id=correlation_id,
                payload={"protected": target, "source": "MANUAL_SELECTION"},
                flush=False,
            )
        append_event(
            session,
            event_type="media.manual_protection_batch_changed",
            entity_type="media_selection",
            entity_id=None,
            actor_type="admin",
            actor_id=admin.id,
            correlation_id=correlation_id,
            payload={
                "protected": target,
                "selection_scope": selection_scope,
                "matched": len(lifecycles),
                "changed": len(changed),
                "filters": filter_query if selection_scope == "filtered" else {},
            },
            flush=False,
        )
        session.commit()

        matched = len(lifecycles)
        changed_count = len(changed)
        matched_noun = "lifecycle" if matched == 1 else "lifecycles"
        changed_noun = "lifecycle" if changed_count == 1 else "lifecycles"
        if changed_count == 0:
            message = f"No manual protection changes were needed for {matched} {matched_noun}."
        elif selection_scope == "filtered" and changed_count == matched:
            action = "applied to" if target else "removed from"
            message = (
                f"Manual protection {action} all {matched} filtered {matched_noun}."
            )
        elif changed_count == matched:
            action = "applied to" if target else "removed from"
            message = f"Manual protection {action} {changed_count} {changed_noun}."
        else:
            action = "applied to" if target else "removed from"
            message = (
                f"Manual protection {action} {changed_count} of {matched} selected "
                f"{matched_noun}; the remainder already had the requested state."
            )
        return _media_redirect(message=message, query=filter_query)

    @app.post("/settings/rollout-mode")
    def settings_rollout_mode_update(
        request: Request,
        rollout_mode: str = Form(),
        confirm_rollout: str | None = Form(None),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        policy = get_rollout_policy(session)
        try:
            previous, current = change_rollout_mode(
                policy,
                rollout_mode,
                confirmed=confirm_rollout == "yes",
            )
        except ValueError as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
        if current != previous:
            invalidate_queued_jobs(session, reason="ROLLOUT_MODE_CHANGED")
        if current != "APPROVAL_REQUIRED":
            for integration in session.scalars(
                select(IntegrationInstance).where(
                    IntegrationInstance.active_management_enabled.is_(True)
                )
            ):
                integration.active_management_enabled = False
        append_event(
            session,
            event_type="rollout.mode_changed",
            entity_type="rollout_policy",
            entity_id=policy.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={"previous": previous, "current": current},
        )
        session.commit()
        return _integrations_redirect(
            message=f"Global rollout is now {current.replace('_', ' ').title()}."
        )

    @app.post("/media/policy")
    @app.post("/settings/policy")
    def settings_policy_update(
        request: Request,
        meaningful_minutes: int = Form(),
        meaningful_percent: int = Form(),
        never_watched_weeks: int = Form(),
        watched_weeks: int = Form(),
        protected_tag_name: str = Form(),
        tautulli_fresh_minutes: int = Form(),
        torrent_fresh_minutes: int = Form(),
        arr_fresh_minutes: int = Form(),
        overseerr_fresh_minutes: int = Form(),
        plex_fresh_minutes: int = Form(),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        values = {
            "meaningful_minutes": meaningful_minutes,
            "meaningful_percent": meaningful_percent,
            "never_watched_weeks": never_watched_weeks,
            "watched_weeks": watched_weeks,
            "tautulli_fresh_minutes": tautulli_fresh_minutes,
            "torrent_fresh_minutes": torrent_fresh_minutes,
            "arr_fresh_minutes": arr_fresh_minutes,
            "overseerr_fresh_minutes": overseerr_fresh_minutes,
            "plex_fresh_minutes": plex_fresh_minutes,
        }
        if any(value < 1 or value > 525600 for value in values.values()):
            return _integrations_redirect(
                error="Policy values must be positive and within one year."
            )
        if meaningful_percent > 100:
            return _integrations_redirect(
                error="Meaningful playback percent cannot exceed 100."
            )
        normalized_tag = protected_tag_name.strip()
        if not normalized_tag or len(normalized_tag) > 120:
            return _integrations_redirect(
                error="Protected Arr tag must be 1 to 120 characters."
            )
        policy = get_inventory_policy(session)
        for key, value in values.items():
            setattr(policy, key, value)
        policy.protected_tag_name = normalized_tag
        for source in session.scalars(select(SourceFreshness)).all():
            minutes = {
                "TAUTULLI": policy.tautulli_fresh_minutes,
                "QBITTORRENT": policy.torrent_fresh_minutes,
                "RADARR": policy.arr_fresh_minutes,
                "SONARR": policy.arr_fresh_minutes,
                "OVERSEERR": policy.overseerr_fresh_minutes,
                "PLEX": policy.plex_fresh_minutes,
            }[source.source_kind]
            source.stale_after_seconds = minutes * 60
        recompute_decisions(session)
        append_event(
            session,
            event_type="inventory.policy_changed",
            entity_type="inventory_policy",
            entity_id=policy.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={**values, "protected_tag_name": normalized_tag},
        )
        session.commit()
        return _integrations_redirect(
            message="Retention settings saved. Existing evidence was re-evaluated fail-closed."
        )

    @app.post("/settings/tracker-policy")
    def settings_tracker_policy_update(
        request: Request,
        domain: str = Form(),
        combination: str = Form(),
        minimum_ratio: str = Form(""),
        minimum_seed_days: str = Form(""),
        grace_hours: str = Form("0"),
        automatic_deletion_allowed: str | None = Form(None),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        normalized_domain = normalize_tracker_domain(domain)
        known_domains = {item[0] for item in discovered_tracker_domains(session)}
        existing_policy = session.scalar(
            select(TrackerPolicy).where(
                TrackerPolicy.normalized_domain == normalized_domain
            )
        )
        if normalized_domain not in known_domains and existing_policy is None:
            return _integrations_redirect(error="Choose a tracker discovered by qBittorrent.")
        try:
            ratio = float(minimum_ratio) if minimum_ratio.strip() else None
            seed_days = float(minimum_seed_days) if minimum_seed_days.strip() else None
            grace = float(grace_hours) if grace_hours.strip() else 0.0
        except ValueError:
            return _integrations_redirect(
                error="Ratio, seed time, and grace period must be valid numbers."
            )
        if seed_days is not None and seed_days < 0:
            return _integrations_redirect(error="Minimum seed time cannot be negative.")
        if seed_days is not None and not seed_days.is_integer():
            return _integrations_redirect(
                error="Minimum seed time must be a whole number of days."
            )
        if grace < 0:
            return _integrations_redirect(error="Grace period cannot be negative.")
        try:
            tracker_policy = save_tracker_policy(
                session,
                domain=normalized_domain,
                minimum_ratio=ratio,
                minimum_seed_seconds=(
                    round(seed_days * 86_400) if seed_days is not None else None
                ),
                combination=combination,
                grace_period_seconds=round(grace * 3600),
                automatic_deletion_allowed=automatic_deletion_allowed == "yes",
            )
        except ValueError as exc:
            session.rollback()
            return _integrations_redirect(error=str(exc))
        append_event(
            session,
            event_type="tracker.policy_changed",
            entity_type="tracker_policy",
            entity_id=tracker_policy.id,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "normalized_domain": normalized_domain,
                "minimum_ratio": tracker_policy.minimum_ratio,
                "minimum_seed_seconds": tracker_policy.minimum_seed_seconds,
                "combination": tracker_policy.combination,
                "grace_period_seconds": tracker_policy.grace_period_seconds,
                "automatic_deletion_allowed": tracker_policy.automatic_deletion_allowed,
            },
        )
        session.commit()
        return _integrations_redirect(
            message=(
                f"Tracker policy for {normalized_domain} saved. "
                "Recalculate the deletion preview to apply it to the queue."
            )
        )

    @app.post("/settings/tracker-selection")
    def settings_tracker_selection_update(
        request: Request,
        domains: list[str] | None = Form(None),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        policies = {
            policy.normalized_domain: policy
            for policy in session.scalars(select(TrackerPolicy)).all()
        }
        known_domains = {domain for domain, _ in discovered_tracker_domains(session)} | set(
            policies
        )
        selected_domains = {
            normalize_tracker_domain(domain) for domain in (domains or [])
        }
        if "" in selected_domains or not selected_domains.issubset(known_domains):
            return _integrations_redirect(
                error="Choose only trackers discovered by qBittorrent."
            )
        for domain, policy in policies.items():
            policy.selected = domain in selected_domains
        for domain in selected_domains - set(policies):
            policies[domain] = save_tracker_policy(
                session,
                domain=domain,
                minimum_ratio=1.0,
                minimum_seed_seconds=10 * 86_400,
                combination="RATIO_OR_TIME",
                grace_period_seconds=12 * 3_600,
                automatic_deletion_allowed=False,
            )
        session.flush()
        append_event(
            session,
            event_type="tracker.selection_changed",
            entity_type="tracker_policy",
            entity_id="selection",
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "selected_domains": sorted(selected_domains),
            },
        )
        session.commit()
        count = len(selected_domains)
        return _integrations_redirect(
            message=f"Tracker selection saved. {count} rule{'s' if count != 1 else ''} shown."
        )

    @app.post("/deletion-queue/recalculate")
    def deletion_queue_recalculate(
        request: Request,
        csrf: str = Form(),
        state: str = Form(""),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        summary = evaluate_dry_run(session)
        append_event(
            session,
            event_type="dry_run.evaluated",
            entity_type="dry_run",
            entity_id=None,
            actor_type="admin",
            actor_id=admin.id,
            payload={
                "evaluated": summary.evaluated,
                "eligible": summary.eligible,
                "blocked": summary.blocked,
                "retained": summary.retained,
                "protected": summary.protected,
                "estimated_bytes": summary.estimated_bytes,
                "external_mutations": 0,
            },
        )
        session.commit()
        message = (
            f"Dry run evaluated {summary.evaluated} downloaded items: "
            f"{summary.eligible} eligible and {summary.blocked} blocked. "
            "No external changes were made."
        )
        query = {"message": message}
        if state.upper() in DRY_RUN_STATES:
            query["state"] = state.upper()
        return RedirectResponse(
            f"/deletion-queue?{urlencode(query)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.post("/deletion-jobs")
    def deletion_job_create(
        request: Request,
        lifecycle_id: str = Form(),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        try:
            job = create_movie_job(
                session,
                lifecycle_id=lifecycle_id,
                admin_id=admin.id,
            )
        except DeletionJobError as error:
            session.rollback()
            return _deletion_redirect(error=str(error))
        return _deletion_redirect(
            job_id=job.id,
            message="Approval case prepared. No external service was changed.",
        )

    @app.get("/deletion-jobs/{job_id}", response_class=HTMLResponse)
    def deletion_job_detail(
        job_id: str,
        request: Request,
        message: str | None = None,
        error: str | None = None,
        warning: str | None = None,
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        record = session.execute(
            select(DeletionJob, MediaLifecycle, MediaIdentity, IntegrationInstance)
            .join(MediaLifecycle, MediaLifecycle.id == DeletionJob.lifecycle_id)
            .join(MediaIdentity, MediaIdentity.id == MediaLifecycle.identity_id)
            .join(IntegrationInstance, IntegrationInstance.id == MediaLifecycle.integration_id)
            .where(DeletionJob.id == job_id)
        ).one_or_none()
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        job, lifecycle, identity, integration = record
        events = session.scalars(
            select(EventRecord)
            .where(EventRecord.correlation_id == job.correlation_id)
            .order_by(EventRecord.occurred_at)
        ).all()
        return _render(
            request,
            "deletion_job.html",
            {
                "admin": admin,
                "job": job,
                "lifecycle": lifecycle,
                "identity": identity,
                "integration": integration,
                "events": events,
                "rollout_policy": get_rollout_policy(session),
                "message": message,
                "error": error,
                "warning": warning,
            },
        )

    @app.post("/deletion-jobs/{job_id}/approve")
    def deletion_job_approve(
        job_id: str,
        request: Request,
        confirm_title: str = Form(),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        job = session.get(DeletionJob, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            approve_movie_job(
                session,
                job,
                admin_id=admin.id,
                confirmation_title=confirm_title,
            )
        except DeletionJobError as error:
            session.rollback()
            return _deletion_redirect(job_id=job.id, error=str(error))
        return _deletion_redirect(
            job_id=job.id,
            message="Deletion approved. Nothing has been deleted yet.",
        )

    @app.post("/deletion-jobs/{job_id}/execute")
    def deletion_job_execute(
        job_id: str,
        request: Request,
        acknowledge: str | None = Form(None),
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        job = session.get(DeletionJob, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if acknowledge != "yes":
            return _deletion_redirect(
                job_id=job.id,
                error="Confirm that this action removes the Radarr movie and its library files.",
            )
        try:
            title = session.scalar(
                select(MediaIdentity.canonical_title)
                .join(MediaLifecycle, MediaLifecycle.identity_id == MediaIdentity.id)
                .join(DeletionJob, DeletionJob.lifecycle_id == MediaLifecycle.id)
                .where(DeletionJob.id == job.id)
            )
            with request.app.state.sync_coordinator.acquire(
                job.id,
                f"Deletion: {title or 'movie'}",
                trigger="manual_deletion",
            ):
                execute_movie_job(
                    session,
                    job,
                    request.app.state.credential_cipher,
                    admin_id=admin.id,
                )
        except SyncAlreadyRunning:
            return _deletion_redirect(
                job_id=job.id,
                error=(
                    "Another inventory or deletion operation is already running. "
                    "Try again after it finishes."
                ),
            )
        except (DeletionJobError, DeletionBlocked) as error:
            return _deletion_redirect(job_id=job.id, error=str(error))
        if job.state == "RECONCILE_REQUIRED":
            return _deletion_redirect(
                job_id=job.id,
                warning=(
                    "Radarr deletion completed, but Plex or Overseerr still needs to converge. "
                    "Retry reconciliation after their scans finish."
                ),
            )
        return _deletion_redirect(
            job_id=job.id,
            message="Approved deletion completed and requestability was confirmed.",
        )

    @app.post("/deletion-jobs/{job_id}/reconcile")
    def deletion_job_reconcile(
        job_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        job = session.get(DeletionJob, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            with request.app.state.sync_coordinator.acquire(
                job.id,
                "Deletion reconciliation",
                trigger="deletion_reconciliation",
            ):
                retry_movie_reconciliation(
                    session,
                    job,
                    request.app.state.credential_cipher,
                    admin_id=admin.id,
                )
        except SyncAlreadyRunning:
            return _deletion_redirect(
                job_id=job.id,
                error=(
                    "Another inventory or deletion operation is already running. "
                    "Try again after it finishes."
                ),
            )
        except DeletionJobError as error:
            return _deletion_redirect(job_id=job.id, error=str(error))
        if job.state == "RECONCILE_REQUIRED":
            return _deletion_redirect(
                job_id=job.id,
                warning="Plex or Overseerr still reports the movie. Try again after scans finish.",
            )
        return _deletion_redirect(
            job_id=job.id,
            message="Plex absence and Overseerr requestability confirmed.",
        )

    @app.post("/deletion-jobs/{job_id}/cancel")
    def deletion_job_cancel(
        job_id: str,
        request: Request,
        csrf: str = Form(),
        session: Session = Depends(_session),
    ):
        verify_csrf(request, csrf)
        admin = _require_admin(request, session)
        job = session.get(DeletionJob, job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        try:
            cancel_movie_job(session, job, admin_id=admin.id)
        except DeletionJobError as error:
            session.rollback()
            return _deletion_redirect(job_id=job.id, error=str(error))
        return _deletion_redirect(
            job_id=job.id,
            message="Approval case cancelled. No external service was changed.",
        )

    @app.get("/media/{lifecycle_id}", response_class=HTMLResponse)
    def media_detail(
        lifecycle_id: str,
        request: Request,
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        record = session.execute(
            select(MediaLifecycle, MediaIdentity, IntegrationInstance)
            .join(MediaIdentity, MediaLifecycle.identity_id == MediaIdentity.id)
            .join(
                IntegrationInstance,
                MediaLifecycle.integration_id == IntegrationInstance.id,
            )
            .where(MediaLifecycle.id == lifecycle_id)
        ).one_or_none()
        if record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        lifecycle, identity, integration = record
        files = session.scalars(
            select(MediaFileRevision)
            .where(MediaFileRevision.lifecycle_id == lifecycle.id)
            .order_by(MediaFileRevision.imported_at)
        ).all()
        mapped = session.execute(
            select(TorrentMediaMapping, Torrent)
            .join(Torrent, TorrentMediaMapping.torrent_id == Torrent.id)
            .where(
                TorrentMediaMapping.lifecycle_id == lifecycle.id,
                Torrent.present.is_(True),
            )
        ).all()
        shared_media_by_torrent: dict[
            str,
            list[tuple[TorrentMediaMapping, MediaLifecycle, MediaIdentity, IntegrationInstance]],
        ] = {}
        mapped_torrent_ids = [torrent.id for _mapping, torrent in mapped]
        if mapped_torrent_ids:
            shared_rows = session.execute(
                select(
                    TorrentMediaMapping,
                    MediaLifecycle,
                    MediaIdentity,
                    IntegrationInstance,
                )
                .join(
                    MediaLifecycle,
                    TorrentMediaMapping.lifecycle_id == MediaLifecycle.id,
                )
                .join(MediaIdentity, MediaLifecycle.identity_id == MediaIdentity.id)
                .join(
                    IntegrationInstance,
                    MediaLifecycle.integration_id == IntegrationInstance.id,
                )
                .where(
                    TorrentMediaMapping.torrent_id.in_(mapped_torrent_ids),
                    TorrentMediaMapping.lifecycle_id != lifecycle.id,
                )
                .order_by(
                    MediaIdentity.canonical_title,
                    MediaIdentity.season_number,
                    IntegrationInstance.name,
                )
            ).all()
            for other_mapping, other_lifecycle, other_identity, other_integration in shared_rows:
                shared_media_by_torrent.setdefault(other_mapping.torrent_id, []).append(
                    (
                        other_mapping,
                        other_lifecycle,
                        other_identity,
                        other_integration,
                    )
                )
        trackers_by_torrent: dict[str, list[TorrentTracker]] = {}
        for _mapping, torrent in mapped:
            trackers_by_torrent[torrent.id] = session.scalars(
                select(TorrentTracker).where(TorrentTracker.torrent_id == torrent.id)
            ).all()
        playbacks: list[Playback] = []
        if lifecycle.plex_rating_key:
            playbacks = session.scalars(
                select(Playback)
                .where(
                    Playback.meaningful.is_(True),
                    (Playback.plex_rating_key == lifecycle.plex_rating_key)
                    | (Playback.parent_rating_key == lifecycle.plex_rating_key),
                )
                .order_by(Playback.watched_at.desc())
                .limit(20)
            ).all()
        request_filters = []
        if identity.tmdb_id is not None:
            request_filters.append(RequestRecord.tmdb_id == identity.tmdb_id)
        if identity.tvdb_id is not None:
            request_filters.append(RequestRecord.tvdb_id == identity.tvdb_id)
        requests = []
        if request_filters:
            condition = request_filters[0]
            for item in request_filters[1:]:
                condition = condition | item
            requests = session.execute(
                select(RequestRecord, RequesterProfile)
                .outerjoin(
                    RequesterProfile,
                    (RequesterProfile.integration_id == RequestRecord.integration_id)
                    & (RequesterProfile.external_id == RequestRecord.requester_id),
                )
                .where(condition, RequestRecord.present.is_(True))
            ).all()
        freshness = session.scalars(
            select(SourceFreshness).order_by(SourceFreshness.source_kind)
        ).all()
        return _render(
            request,
            "media_detail.html",
            {
                "admin": admin,
                "lifecycle": lifecycle,
                "identity": identity,
                "integration": integration,
                "files": files,
                "mapped": mapped,
                "shared_media_by_torrent": shared_media_by_torrent,
                "trackers_by_torrent": trackers_by_torrent,
                "playbacks": playbacks,
                "requests": requests,
                "freshness": freshness,
                "source_is_fresh": source_is_fresh,
            },
        )

    return app


app = create_app()
