from __future__ import annotations

import json
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import quote, urlparse

from fastapi import BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session

from . import API_VERSION, SCHEMA_VERSION, __version__
from .backup import create_backup, validate_monitor_output_target
from .config import Settings, process_start_ticks
from .database import Database, get_database
from .models import Artifact
from .mutations import operation_integrity_error
from .proposals import AppService
from .preview import SafeOpenError, render_markdown_document
from .schemas import (
    LayoutMutationEnvelope, MutationEnvelope, MutationUndo, ProjectCreate, ProposalApply,
    ProposalEnvelope, ProposalReject, ProposalRevision,
)
from .service import DomainError


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _artifact_content_disposition(name: str) -> str:
    fallback = "".join(
        character
        if 32 <= ord(character) < 127 and character not in {'"', "\\"}
        else "_"
        for character in name
    ).strip() or "artifact"
    encoded = quote(name, safe="")
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


class ServerStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str = Field(min_length=1, max_length=200)


def _local_hostname(value: str) -> str | None:
    hostname, separator, raw_port = value.casefold().partition(":")
    if hostname not in {"127.0.0.1", "localhost", "testserver"}:
        return None
    if separator:
        try:
            port = int(raw_port)
        except ValueError:
            return None
        if not 1 <= port <= 65535:
            return None
    return hostname


def _expected_hosts(scope: dict[str, Any]) -> set[str]:
    server = scope.get("server") or ("127.0.0.1", 80)
    hostname, port = str(server[0]).casefold(), int(server[1])
    scheme = str(scope.get("scheme", "http")).casefold()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    hostnames = {hostname}
    if hostname in {"127.0.0.1", "localhost"}:
        hostnames.update({"127.0.0.1", "localhost"})
    suffix = "" if default_port else f":{port}"
    return {f"{candidate}{suffix}" for candidate in hostnames}


def _is_direct_browser_navigation(request: Request) -> bool:
    """Recognize a user-launched top-level document navigation."""

    return (
        request.headers.get("sec-fetch-site", "").casefold() == "none"
        and request.headers.get("sec-fetch-mode", "").casefold() == "navigate"
        and request.headers.get("sec-fetch-dest", "").casefold() == "document"
        and request.headers.get("sec-fetch-user", "").casefold() == "?1"
    )


class BrowserAuthState:
    """One-use bootstrap capabilities and process-local browser sessions."""

    def __init__(
        self,
        bootstrap_token: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._clock = clock
        # The serve-time token remains valid until first use so a user can copy
        # the printed URL manually. Recovery tokens minted by the CLI are short-lived.
        self._bootstrap_tokens: dict[str, float | None] = {bootstrap_token: None}
        self._sessions: dict[str, str] = {}
        self._lock = threading.Lock()

    def mint_bootstrap(self, ttl_seconds: int = 60) -> str:
        if ttl_seconds <= 0:
            raise ValueError("Bootstrap lifetime must be positive")
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._bootstrap_tokens = {
                candidate: expires_at
                for candidate, expires_at in self._bootstrap_tokens.items()
                if expires_at is None or expires_at > now
            }
            self._bootstrap_tokens[token] = now + ttl_seconds
        return token

    def consume_bootstrap(self, token: str) -> tuple[str, str] | None:
        with self._lock:
            now = self._clock()
            matched = next(
                (
                    candidate
                    for candidate in self._bootstrap_tokens
                    if secrets.compare_digest(token, candidate)
                ),
                None,
            )
            if matched is None:
                return None
            expires_at = self._bootstrap_tokens.pop(matched)
            if expires_at is not None and expires_at <= now:
                return None
            return self._create_session_locked()

    def create_direct_session(self) -> tuple[str, str]:
        """Create a session after strict direct-navigation validation."""

        with self._lock:
            return self._create_session_locked()

    def _create_session_locked(self) -> tuple[str, str]:
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        self._sessions[session_token] = csrf_token
        return session_token, csrf_token

    def csrf_for_session(self, session_token: str | None) -> str | None:
        if not session_token:
            return None
        with self._lock:
            return self._sessions.get(session_token)


def _safe_local_navigation_target(request: Request) -> str:
    """Return the current local path/query without permitting an external redirect."""

    path = request.url.path
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or any(ord(character) < 0x20 or ord(character) == 0x7f for character in path)
    ):
        return "/"
    query = request.url.query
    if any(ord(character) < 0x20 or ord(character) == 0x7f for character in query):
        query = ""
    return f"{path}?{query}" if query else path


def _browser_session_redirect(
    credentials: tuple[str, str],
    target: str = "/",
) -> RedirectResponse:
    session_token, csrf_token = credentials
    response = RedirectResponse(url=target, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        "research_monitor_session", session_token, httponly=True,
        samesite="strict", path="/",
    )
    response.set_cookie(
        "research_monitor_csrf", csrf_token, httponly=False,
        samesite="strict", path="/",
    )
    return response


class LocalSecurityMiddleware:
    def __init__(self, app: Any, settings: Settings, browser_auth: BrowserAuthState):
        self.app = app
        self.settings = settings
        self.browser_auth = browser_auth

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send); return
        request = Request(scope, receive=receive)
        host = request.headers.get("host", "").casefold()
        request_hostname = _local_hostname(host)
        server = scope.get("server") or ("127.0.0.1", 80)
        server_hostname = str(server[0]).casefold()
        forwarded_loopback = request_hostname in {"127.0.0.1", "localhost"} and server_hostname in {"127.0.0.1", "localhost"}
        if request_hostname is None or (not forwarded_loopback and host not in _expected_hosts(scope)):
            await JSONResponse(status_code=400, content={"detail": {"code": "invalid_host", "message": "Only local Host headers are accepted"}})(scope, receive, send); return
        authorization = request.headers.get("authorization", "")
        is_cli = bool(self.settings.cli_token) and secrets.compare_digest(
            authorization, f"Bearer {self.settings.cli_token}"
        )
        browser_session = request.cookies.get("research_monitor_session")
        browser_csrf = self.browser_auth.csrf_for_session(browser_session)
        is_browser = browser_csrf is not None
        is_api = request.url.path == "/api/v1" or request.url.path.startswith("/api/v1/")
        if is_api and not (is_cli or is_browser):
            await JSONResponse(status_code=401, content={"detail": {"code": "authentication_required", "message": "Authenticate through the browser bootstrap URL or CLI token"}})(scope, receive, send); return
        scope.setdefault("state", {})["research_monitor_auth"] = "cli" if is_cli else "browser" if is_browser else "public"
        origin = request.headers.get("origin")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme.casefold() != str(scope.get("scheme", "http")).casefold() or parsed.netloc.casefold() != host:
                await JSONResponse(status_code=403, content={"detail": {"code": "invalid_origin", "message": "Cross-origin requests are not accepted"}})(scope, receive, send); return
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
            if media_type != "application/json":
                await JSONResponse(status_code=415, content={"detail": {"code": "json_required", "message": "Mutations require application/json"}})(scope, receive, send); return
            cookie_token = request.cookies.get("research_monitor_csrf")
            if not is_cli and (
                not is_browser
                or not cookie_token
                or not secrets.compare_digest(cookie_token, browser_csrf or "")
                or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), browser_csrf or "")
            ):
                await JSONResponse(status_code=403, content={"detail": {"code": "csrf_failed", "message": "Invalid CSRF token"}})(scope, receive, send); return

        async def secure_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}
                for key, value in SECURITY_HEADERS.items():
                    encoded = key.lower().encode("latin-1")
                    if encoded not in existing: headers.append((encoded, value.encode("latin-1")))
                if is_browser and request.method == "GET" and not request.cookies.get("research_monitor_csrf"):
                    cookie = f"research_monitor_csrf={browser_csrf}; Path=/; SameSite=Strict"
                    headers.append((b"set-cookie", cookie.encode("latin-1")))
                message["headers"] = headers
            await send(message)
        await self.app(scope, receive, secure_send)


def create_app(
    settings: Settings | None = None,
    database: Database | None = None,
    frontend_dir: Path | None = None,
    browser_bootstrap_token: str | None = None,
    server_instance_id: str | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> FastAPI:
    settings = settings or Settings.load()
    database = database or get_database(settings)
    service = AppService(settings)
    event_stream_id = secrets.token_urlsafe(24)
    server_instance_id = server_instance_id or secrets.token_urlsafe(32)
    server_pid = os.getpid()
    server_process_start_ticks = process_start_ticks(server_pid)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    app = FastAPI(title="Research Monitor", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    browser_auth = BrowserAuthState(browser_bootstrap_token or secrets.token_urlsafe(32))
    app.state.settings = settings; app.state.database = database; app.state.service = service; app.state.browser_auth = browser_auth; app.state.event_stream_id = event_stream_id; app.state.server_instance_id = server_instance_id
    app.add_middleware(LocalSecurityMiddleware, settings=settings, browser_auth=browser_auth)

    @app.get("/__bootstrap/{capability}", include_in_schema=False)
    def browser_bootstrap(capability: str) -> Response:
        credentials = browser_auth.consume_bootstrap(capability)
        if credentials is None:
            raise HTTPException(status_code=404)
        return _browser_session_redirect(credentials)

    @app.post("/api/v1/browser/bootstrap")
    def mint_browser_bootstrap(request: Request) -> dict[str, Any]:
        if request.state.research_monitor_auth != "cli":
            raise DomainError(
                403,
                "cli_auth_required",
                "A fresh browser session can be opened only through the authenticated CLI",
            )
        expires_in_seconds = 60
        capability = browser_auth.mint_bootstrap(expires_in_seconds)
        return {
            "browser_url": str(
                request.url_for("browser_bootstrap", capability=capability)
            ),
            "expires_in_seconds": expires_in_seconds,
        }

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.as_detail()})

    @app.exception_handler(DatabaseError)
    async def database_error_handler(
        _request: Request,
        _exc: DatabaseError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "database_unavailable",
                    "message": (
                        "The monitor database is unavailable. Stop Research Monitor with "
                        "'research-monitor stop', restore a verified backup with "
                        "'research-monitor backup restore <backup.db> --confirm', then restart."
                    ),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "invalid_request",
                    "message": "Request validation failed",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    def session_dependency() -> Any:
        with database.session() as session:
            yield session

    def write_session_dependency() -> Any:
        with database.write_session() as session:
            yield session

    SessionDep = Depends(session_dependency)
    WriteSessionDep = Depends(write_session_dependency)

    @app.get("/api/v1/version")
    def version() -> dict[str, Any]:
        return {
            "api_version": API_VERSION,
            "schema_version": SCHEMA_VERSION,
            "version": __version__,
            "server_instance_id": server_instance_id,
            "server_pid": server_pid,
            "process_start_ticks": server_process_start_ticks,
        }

    @app.post("/api/v1/server/stop")
    def stop_server(
        payload: ServerStopRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> dict[str, Any]:
        if request.state.research_monitor_auth != "cli":
            raise DomainError(
                403,
                "cli_auth_required",
                "The server can be stopped only through the authenticated CLI",
            )
        if not secrets.compare_digest(payload.instance_id, server_instance_id):
            raise DomainError(
                409,
                "server_instance_mismatch",
                "Running server instance does not match the requested instance",
            )
        if shutdown_callback is None:
            raise DomainError(
                503,
                "server_stop_unavailable",
                "This server process does not expose a shutdown callback",
            )
        background_tasks.add_task(shutdown_callback)
        return {
            "stopping": True,
            "instance_id": server_instance_id,
            "pid": server_pid,
        }

    @app.get("/api/v1/projects")
    def projects(include_archived: bool = True, include_trashed: bool = False, session: Session = SessionDep) -> dict[str, Any]:
        return {"projects": service.list_projects(session, include_archived, include_trashed)}

    @app.post("/api/v1/projects", status_code=201)
    def create_project(payload: ProjectCreate, session: Session = WriteSessionDep) -> dict[str, Any]:
        return {"project": service.create_project(session, payload)}

    @app.get("/api/v1/projects/resolve")
    def resolve_project(path: str = Query(...), session: Session = SessionDep) -> dict[str, Any]:
        return service.resolve_project(session, path)

    @app.get("/api/v1/projects/{project_id}/snapshot")
    def project_snapshot(
        project_id: str,
        sections: str | None = Query(None, max_length=500),
        session: Session = SessionDep,
    ) -> dict[str, Any]:
        requested: set[str] | None = None
        if sections is not None:
            requested = {value.strip() for value in sections.split(",") if value.strip()}
            if not requested:
                raise DomainError(
                    422,
                    "invalid_snapshot_section",
                    "Snapshot sections cannot be empty",
                )
        return service.snapshot(session, project_id, requested)

    @app.get("/api/v1/projects/{project_id}/search")
    def project_search(
        project_id: str,
        q: str = Query(..., min_length=1, max_length=500),
        entity_type: list[str] | None = Query(None),
        status: str | None = Query(None),
        priority: str | None = Query(None),
        readiness: str | None = Query(None),
        label: str | None = Query(None, max_length=200),
        artifact_type: str | None = Query(None),
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100_000),
        session: Session = SessionDep,
    ) -> dict[str, Any]:
        return service.search(
            session,
            project_id,
            q,
            entity_types=set(entity_type) if entity_type else None,
            status=status,
            priority=priority,
            readiness_state=readiness,
            label=label,
            artifact_type=artifact_type,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/projects/{project_id}/agent-context")
    def agent_context(project_id: str, session: Session = SessionDep) -> dict[str, Any]:
        return service.agent_context(session, project_id)

    @app.get("/api/v1/projects/{project_id}/export")
    def export_project(project_id: str, output_path: str | None = None, session: Session = SessionDep) -> dict[str, Any]:
        if output_path is not None:
            validate_monitor_output_target(database, Path(output_path), purpose="export")
        return service.export_project(session, project_id)

    @app.get("/api/v1/projects/{project_id}/history")
    def project_history(project_id: str, limit: int = Query(500, ge=1, le=2000), session: Session = SessionDep) -> dict[str, Any]:
        return {"events": service.history(session, project_id, limit)}

    @app.post("/api/v1/projects/{project_id}/mutations")
    def mutate(project_id: str, payload: MutationEnvelope, session: Session = WriteSessionDep) -> dict[str, Any]:
        if str(payload.project_id) != project_id: raise DomainError(422, "project_mismatch", "Envelope project_id does not match route")
        payload = payload.model_copy(update={"actor_type": "ui"})
        return service.mutate(session, payload)

    @app.post("/api/v1/projects/{project_id}/mutations/{target_request_id}/undo")
    def undo_mutation(
        project_id: str,
        target_request_id: str,
        payload: MutationUndo,
        session: Session = WriteSessionDep,
    ) -> dict[str, Any]:
        return service.undo(session, project_id, target_request_id, payload)

    @app.post("/api/v1/projects/{project_id}/layout-mutations")
    def mutate_layout(project_id: str, payload: LayoutMutationEnvelope, session: Session = WriteSessionDep) -> dict[str, Any]:
        if str(payload.project_id) != project_id: raise DomainError(422, "project_mismatch", "Envelope project_id does not match route")
        payload = payload.model_copy(update={"actor_type": "ui"})
        return service.mutate_layout(session, payload)

    @app.get("/api/v1/projects/{project_id}/proposals")
    def proposals(project_id: str, session: Session = SessionDep) -> dict[str, Any]:
        return {"proposals": service.proposals(session, project_id)}

    @app.post("/api/v1/projects/{project_id}/proposals/validate")
    def validate_proposal(project_id: str, payload: ProposalEnvelope, session: Session = WriteSessionDep) -> dict[str, Any]:
        return service.validate_proposal(session, project_id, payload)

    @app.post("/api/v1/projects/{project_id}/proposals", status_code=201)
    def create_proposal(project_id: str, payload: ProposalEnvelope, session: Session = WriteSessionDep) -> dict[str, Any]:
        try:
            return service.create_proposal(session, project_id, payload)
        except IntegrityError as exc:
            translated = operation_integrity_error(exc)
            if translated is None:
                raise
            raise translated from exc

    @app.post(
        "/api/v1/projects/{project_id}/proposals/{proposal_id}/revisions",
        status_code=201,
    )
    def revise_proposal(
        project_id: str,
        proposal_id: str,
        payload: ProposalRevision,
        session: Session = WriteSessionDep,
    ) -> dict[str, Any]:
        try:
            return service.revise_proposal(session, project_id, proposal_id, payload)
        except IntegrityError as exc:
            translated = operation_integrity_error(exc)
            if translated is None:
                raise
            raise translated from exc

    @app.get("/api/v1/proposals/{proposal_id}")
    def inspect_proposal(proposal_id: str, session: Session = SessionDep) -> dict[str, Any]:
        return service.proposal(session, proposal_id)

    @app.post("/api/v1/projects/{project_id}/proposals/{proposal_id}/apply")
    def apply_proposal(project_id: str, proposal_id: str, payload: ProposalApply, session: Session = WriteSessionDep) -> Response:
        try:
            value = service.apply_proposal(session, project_id, proposal_id, payload)
            return JSONResponse(value)
        except DomainError as exc:
            if exc.code == "revision_conflict":
                # Returning normally lets the write dependency commit the durable
                # conflict/operation dispositions before the 409 reaches the UI.
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.as_detail()})
            raise

    @app.post("/api/v1/projects/{project_id}/proposals/{proposal_id}/reject")
    def reject_proposal(project_id: str, proposal_id: str, payload: ProposalReject, session: Session = WriteSessionDep) -> dict[str, Any]:
        return service.reject_proposal(session, project_id, proposal_id, str(payload.request_id), payload.reason)

    @app.get("/api/v1/artifacts/{artifact_id}/metadata")
    def artifact_metadata(artifact_id: str, session: Session = SessionDep) -> dict[str, Any]:
        value = service.artifact_metadata(session, artifact_id); value.pop("path", None); return value

    @app.get("/api/v1/artifacts/{artifact_id}/preview")
    def artifact_preview(artifact_id: str, session: Session = SessionDep) -> Response:
        opened = service.artifact_preview(session, artifact_id)
        try:
            content = opened.read_all()
        except SafeOpenError as exc:
            raise DomainError(exc.status_code, exc.code, exc.message) from exc
        except OSError as exc:
            opened.close()
            raise DomainError(
                404,
                "artifact_changed",
                "Artifact changed before it could be read",
            ) from exc

        headers = {
            "Content-Disposition": _artifact_content_disposition(opened.name),
            "Cache-Control": "no-store",
        }
        if opened.mode in {"text", "pdf", "markdown"}:
            # Safe previews are embedded in a sandboxed same-origin iframe.  The
            # application-wide frame denial would otherwise make text previews
            # unusable, so the artifact response gets a narrower CSP of its own.
            headers["Content-Security-Policy"] = (
                "sandbox; default-src 'none'; style-src 'unsafe-inline'; "
                "frame-ancestors 'self'"
            )
            headers["X-Frame-Options"] = "SAMEORIGIN"
        if opened.mode == "markdown":
            source = content.decode("utf-8", errors="replace")
            return HTMLResponse(render_markdown_document(source), headers=headers)
        headers["Content-Length"] = str(len(content))
        return Response(
            content=content,
            media_type=opened.media_type,
            headers=headers,
        )

    @app.get("/api/v1/events")
    def events(
        request: Request,
        after: int = Query(0, ge=0),
        stream_id: str | None = Query(None, max_length=128),
        stream: bool = False,
        session: Session = SessionDep,
    ) -> Response:
        latest_id = service.latest_event_id(session)
        stream_changed = stream_id is not None and stream_id != event_stream_id
        cursor_ahead = after > latest_id
        reset_required = stream_changed or cursor_ahead
        reset_reason = "stream_changed" if stream_changed else "cursor_ahead" if cursor_ahead else None
        rows = service.events(session, 0 if reset_required else after)
        cursor = {
            "stream_id": event_stream_id,
            "latest_id": latest_id,
            "reset_required": reset_required,
            "reset_reason": reset_reason,
        }
        headers = {
            "X-Research-Monitor-Event-Stream-Id": event_stream_id,
            "X-Research-Monitor-Event-Latest-Id": str(latest_id),
            "X-Research-Monitor-Event-Reset": "true" if reset_required else "false",
        }
        wants_stream = stream or "text/event-stream" in request.headers.get("accept", "")
        if not wants_stream: return JSONResponse({"events": rows, **cursor}, headers=headers)
        async def generate() -> AsyncIterator[str]:
            yield f": cursor {json.dumps(cursor, separators=(',', ':'))}\n\n"
            for row in rows: yield f"id: {row['id']}\nevent: {row['event_type']}\ndata: {json.dumps(row)}\n\n"
            yield "retry: 2000\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", **headers})

    @app.post("/api/v1/backup")
    def backup_create(
        request: Request,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        requested = payload.get("output")
        requested_path = Path(str(requested)).expanduser() if requested else None
        if requested_path is not None:
            validate_monitor_output_target(database, requested_path, purpose="backup")
        if requested and request.state.research_monitor_auth != "cli":
            raise DomainError(
                403, "browser_backup_target_forbidden",
                "Browser backups use the managed backup directory",
            )
        force = payload.get("force", False)
        if not isinstance(force, bool):
            raise DomainError(422, "invalid_backup_force", "Backup force must be a boolean")
        path = create_backup(
            database,
            requested_path,
            force=force,
        )
        return {"path": str(path), "integrity": "ok"}

    packaged_dist = Path(__file__).resolve().parent / "static"
    source_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    dist = frontend_dir or (packaged_dist if (packaged_dist / "index.html").is_file() else source_dist)
    assets = dist / "assets"
    if assets.is_dir(): app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(request: Request, full_path: str) -> Response:
        if full_path.startswith("api/"): raise HTTPException(status_code=404)
        if (
            request.state.research_monitor_auth == "public"
            and _is_direct_browser_navigation(request)
        ):
            return _browser_session_redirect(
                browser_auth.create_direct_session(),
                _safe_local_navigation_target(request),
            )
        if dist.is_dir():
            requested = (dist / full_path).resolve()
            if full_path and requested.is_file() and dist.resolve() in requested.parents: return FileResponse(requested)
            index = dist / "index.html"
            if index.is_file():
                return FileResponse(index, headers={"Cache-Control": "no-store"})
        return HTMLResponse(
            "<!doctype html><html><head><title>Research Monitor</title></head>"
            "<body><main><h1>Research Monitor</h1><p>The API is running. "
            "Build the frontend to use the dashboard.</p></main></body></html>",
            headers={"Cache-Control": "no-store"},
        )

    return app
