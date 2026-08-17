from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.persistence.database import Database
from app.persistence.models import (
    AdminUser,
    EventRecord,
    IntegrationInstance,
    ManagedLibrary,
    MediaFileRevision,
    MediaIdentity,
    MediaLifecycle,
    Playback,
    RequestRecord,
    SourceFreshness,
    Torrent,
    TorrentMediaMapping,
    TorrentTracker,
)
from app.security.auth import (
    LoginRateLimiter,
    csrf_token,
    hash_password,
    verify_csrf,
    verify_password,
)
from app.security.credentials import CredentialCipher
from app.services.events import append_event
from app.services.integrations import (
    change_management_mode,
    create_integration,
    discover_from_overseerr,
    discover_plex_libraries,
    remove_integration_local_data,
    set_active_management,
    test_integration,
    update_integration,
)
from app.services.inventory import (
    get_inventory_policy,
    preview_integration,
    recompute_decisions,
    source_is_fresh,
    sync_integration,
)
from app.services.scheduler import inventory_scheduler_loop
from app.settings import Settings

APP_ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_ROOT / "templates")


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
    values = {
        "request": request,
        "app_name": request.app.state.settings.app_name,
        "csrf_token": csrf_token(request),
        "error": None,
        "errors": {},
        "username": "",
    }
    values.update(context or {})
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=values,
        status_code=status_code,
    )


def _integration_context(session: Session) -> dict:
    integrations = session.scalars(
        select(IntegrationInstance).order_by(IntegrationInstance.kind, IntegrationInstance.name)
    ).all()
    libraries = session.scalars(select(ManagedLibrary).order_by(ManagedLibrary.name)).all()
    libraries_by_plex: dict[str, list[ManagedLibrary]] = {}
    for library in libraries:
        libraries_by_plex.setdefault(library.plex_integration_id, []).append(library)
    freshness_by_integration = {
        row.integration_id: row for row in session.scalars(select(SourceFreshness)).all()
    }
    return {
        "integrations": integrations,
        "libraries_by_plex": libraries_by_plex,
        "freshness_by_integration": freshness_by_integration,
        "source_is_fresh": source_is_fresh,
    }


def _integrations_redirect(*, message: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(
            f"/integrations?error={quote_plus(error)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        f"/integrations?message={quote_plus(message or '')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _media_redirect(*, message: str | None = None, error: str | None = None):
    key = "error" if error else "message"
    value = error or message or ""
    return RedirectResponse(
        f"/media?{key}={quote_plus(value)}", status_code=status.HTTP_303_SEE_OTHER
    )


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
                "rollout_mode": "INVENTORY_ONLY",
            },
        )

    @app.get("/integrations", response_class=HTMLResponse)
    def integrations_page(
        request: Request,
        message: str | None = None,
        error: str | None = None,
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        context = _integration_context(session)
        context.update({"admin": admin, "message": message, "error": error})
        return _render(
            request,
            "integrations.html",
            context,
        )

    @app.post("/integrations")
    def integrations_create(
        request: Request,
        kind: str = Form(),
        name: str = Form(),
        base_url: str = Form(),
        api_key: str = Form(""),
        username: str = Form(""),
        password: str = Form(""),
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
                username=username,
                password=password,
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
            elif "username" in validation_message or "password" in validation_message:
                field = "credentials"
            context = _integration_context(session)
            context.update(
                {
                    "admin": admin,
                    "errors": {field: validation_message},
                    "form": {
                        "kind": kind,
                        "name": name,
                        "base_url": base_url,
                        "username": username,
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
                    "username": "",
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
        username: str = Form(""),
        password: str = Form(""),
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
                username=username,
                password=password,
            )
        except ValueError as validation_error:
            validation_message = str(validation_error)
            field = "credentials"
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
                        "username": username,
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
            return _integrations_redirect(
                error="Active Management is unavailable while rollout mode is Inventory Only."
            )
        try:
            set_active_management(integration, enabled=target)
        except ValueError as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
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
            run = sync_integration(session, integration, request.app.state.credential_cipher)
        except ValueError as error:
            session.rollback()
            return _integrations_redirect(error=str(error))
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
        if run.status == "FAILED":
            return _integrations_redirect(
                error=f"{integration.name} sync failed: {run.sanitized_error}"
            )
        summary = ", ".join(f"{value} {key}" for key, value in run.counts.items())
        return _integrations_redirect(message=f"{integration.name} synchronized: {summary}.")

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
        session: Session = Depends(_session),
    ):
        admin = _require_admin(request, session)
        records = session.execute(
            select(MediaLifecycle, MediaIdentity, IntegrationInstance)
            .join(MediaIdentity, MediaLifecycle.identity_id == MediaIdentity.id)
            .join(
                IntegrationInstance,
                MediaLifecycle.integration_id == IntegrationInstance.id,
            )
            .order_by(MediaLifecycle.retention_deadline, MediaIdentity.canonical_title)
        ).all()
        mapping_counts = dict(
            session.execute(
                select(
                    TorrentMediaMapping.lifecycle_id,
                    func.count(TorrentMediaMapping.id),
                ).group_by(TorrentMediaMapping.lifecycle_id)
            ).all()
        )
        rows = [
            {
                "lifecycle": lifecycle,
                "identity": identity,
                "integration": integration,
                "torrent_count": mapping_counts.get(lifecycle.id, 0),
            }
            for lifecycle, identity, integration in records
        ]
        freshness = session.scalars(
            select(SourceFreshness).order_by(SourceFreshness.source_kind)
        ).all()
        fresh_count = sum(source_is_fresh(item) for item in freshness)
        policy = get_inventory_policy(session)
        return _render(
            request,
            "media.html",
            {
                "admin": admin,
                "rows": rows,
                "freshness": freshness,
                "fresh_count": fresh_count,
                "message": message,
                "error": error,
                "source_is_fresh": source_is_fresh,
                "policy": policy,
            },
        )

    @app.post("/media/policy")
    def media_policy_update(
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
            return _media_redirect(error="Policy values must be positive and within one year.")
        if meaningful_percent > 100:
            return _media_redirect(error="Meaningful playback percent cannot exceed 100.")
        normalized_tag = protected_tag_name.strip()
        if not normalized_tag or len(normalized_tag) > 120:
            return _media_redirect(error="Protected Arr tag must be 1 to 120 characters.")
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
        return _media_redirect(
            message="Inventory policy saved. Existing evidence was re-evaluated fail-closed."
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
            .where(TorrentMediaMapping.lifecycle_id == lifecycle.id)
        ).all()
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
            requests = session.scalars(
                select(RequestRecord).where(condition, RequestRecord.present.is_(True))
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
                "trackers_by_torrent": trackers_by_torrent,
                "playbacks": playbacks,
                "requests": requests,
                "freshness": freshness,
                "source_is_fresh": source_is_fresh,
            },
        )

    return app


app = create_app()
