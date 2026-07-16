from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from uuid import UUID, uuid4

import typer
from typer.main import get_command

try:  # Typer 0.21 vendors Click; older supported releases import it directly.
    from typer.main import _click as typer_click
except ImportError:  # pragma: no cover - compatibility with older Typer releases
    import click as typer_click

from . import API_VERSION, SCHEMA_VERSION, __version__
from .backup import create_backup, restore_backup, validate_monitor_output_target
from .config import Settings, process_start_ticks
from .database import (
    Database,
    DatabaseCompatibilityError,
    DatabaseIntegrityError,
    DatabaseSchemaError,
    get_database,
    reset_database_singleton,
)
from .locking import ApplicationLock
from .lifecycle import purge_project
from .proposals import AppService
from .schemas import ProjectCreate, ProposalEnvelope
from .service import DomainError
from .skill_validation import SkillBundleValidationError, validate_skill_tree
from .transport import RuntimeClient


app = typer.Typer(help="Local research project task monitor", no_args_is_help=True)
project_app = typer.Typer(help="Enroll and inspect projects")
agent_app = typer.Typer(help="Read-only context for coding agents")
proposal_app = typer.Typer(help="Validate and create reviewable agent proposals")
export_app = typer.Typer(help="Export portable monitor data")
backup_app = typer.Typer(help="Create and restore verified SQLite backups")
skill_app = typer.Typer(help="Install the bundled Codex companion skill")
app.add_typer(project_app, name="project")
app.add_typer(agent_app, name="agent")
app.add_typer(proposal_app, name="proposal")
app.add_typer(export_app, name="export")
app.add_typer(backup_app, name="backup")
app.add_typer(skill_app, name="skill")


def _envelope(data: Any = None, error: dict[str, Any] | None = None, request_id: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"api_version": API_VERSION, "schema_version": SCHEMA_VERSION, "request_id": request_id or str(uuid4())}
    if error is not None: value["error"] = error
    else: value["data"] = data
    return value


def _print(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _exit_for(exc: DomainError) -> int:
    if exc.status_code == 409 and exc.code in {"revision_conflict", "entity_version_conflict"}: return 4
    if exc.code in {"project_not_found", "ambiguous_project"}: return 3
    if exc.code in {
        "schema_incompatible",
        "api_incompatible",
        "database_schema_invalid",
    }:
        return 5
    if exc.status_code == 503 or exc.code in {
        "server_unavailable",
        "lock_unavailable",
        "transport_unavailable",
        "application_running",
        "unsafe_runtime_descriptor",
        "backup_integrity_failed",
    }:
        return 6
    return 2


def _database_integrity_error(exc: DatabaseIntegrityError) -> dict[str, Any]:
    return {
        "code": "database_integrity_failed",
        "message": str(exc),
        "details": {"path": str(exc.path), "result": exc.result},
    }


def _database_schema_error(exc: DatabaseSchemaError) -> dict[str, Any]:
    return {
        "code": "database_schema_invalid",
        "message": str(exc),
        "details": {"path": str(exc.path), "detail": exc.detail},
    }


class _DataAccessLocks:
    """Host-local and shared locks held in their canonical acquisition order."""

    def __init__(self, local: ApplicationLock, shared: ApplicationLock):
        self.local = local
        self.shared = shared

    def release(self) -> None:
        # Reverse acquisition order ensures a waiter that obtains the local lock
        # can also obtain the shared lock released immediately before it.
        self.shared.release()
        self.local.release()


def _try_data_access_locks(
    settings: Settings,
    *,
    retained_local: ApplicationLock | None = None,
) -> tuple[_DataAccessLocks | None, str | None, dict[str, Any]]:
    """Acquire local then shared locks, reporting which scope blocked access."""

    local = retained_local or ApplicationLock(settings.lock_path)
    if retained_local is None and not local.acquire():
        owner = getattr(local, "owner_metadata", {})
        return None, "local", dict(owner) if isinstance(owner, dict) else {}

    shared = ApplicationLock(settings.shared_lock_path)
    if not shared.acquire():
        owner = getattr(shared, "owner_metadata", {})
        local.release()
        return None, "shared", dict(owner) if isinstance(owner, dict) else {}
    return _DataAccessLocks(local, shared), None, {}


def _shared_writer_error(
    settings: Settings,
    owner: dict[str, Any],
) -> DomainError:
    return DomainError(
        503,
        "shared_writer_active",
        (
            "Research Monitor data is already in use by another host or process. "
            "Stop that instance before accessing this shared monitor."
        ),
        {
            "lock_path": str(settings.shared_lock_path),
            "owner": owner,
        },
    )


def _require_stopped_data_access(
    settings: Settings,
    *,
    local_message: str,
) -> _DataAccessLocks:
    locks, blocked_by, owner = _try_data_access_locks(settings)
    if locks is not None:
        return locks
    if blocked_by == "shared":
        raise _shared_writer_error(settings, owner)
    raise DomainError(409, "application_running", local_message)


def _invoke(
    callback: Callable[[], Any],
    request_id: str | Callable[[], str | None] | None = None,
) -> None:
    def correlation_id() -> str | None:
        return request_id() if callable(request_id) else request_id

    try: _print(_envelope(callback(), request_id=correlation_id()))
    except DomainError as exc:
        _print(_envelope(error=exc.as_detail(), request_id=correlation_id())); raise typer.Exit(_exit_for(exc)) from exc
    except DatabaseIntegrityError as exc:
        _print(_envelope(error=_database_integrity_error(exc), request_id=correlation_id()))
        raise typer.Exit(6) from exc
    except DatabaseSchemaError as exc:
        _print(_envelope(error=_database_schema_error(exc), request_id=correlation_id()))
        raise typer.Exit(5) from exc
    except DatabaseCompatibilityError as exc:
        error = {
            "code": "schema_incompatible",
            "message": str(exc),
            "details": {"found": exc.found, "expected": exc.expected},
        }
        _print(_envelope(error=error, request_id=correlation_id()))
        raise typer.Exit(5) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print(_envelope(error={"code": "invalid_input", "message": str(exc)}, request_id=correlation_id())); raise typer.Exit(2) from exc


def _verified_client(
    settings: Settings,
    *,
    missing_code: str = "lock_unavailable",
    missing_message: str = "Another process owns the monitor lock but no live server descriptor is available",
) -> RuntimeClient:
    client = RuntimeClient.discover(settings)
    if client is None:
        raise DomainError(503, missing_code, missing_message)
    try:
        version = client.request("GET", "/api/v1/version")
    except (ValueError, TypeError, AttributeError) as exc:
        raise DomainError(
            409,
            "api_incompatible",
            "Running server did not return a compatible version contract",
        ) from exc
    if not isinstance(version, dict):
        raise DomainError(
            409,
            "api_incompatible",
            "Running server did not return a compatible version contract",
        )
    if version.get("api_version") != API_VERSION:
        raise DomainError(409, "api_incompatible", "Running server uses an incompatible API version", version)
    if version.get("schema_version") != SCHEMA_VERSION:
        raise DomainError(409, "schema_incompatible", "Running server uses an incompatible schema version", version)
    if (
        version.get("server_instance_id") != client.instance_id
        or version.get("server_pid") != client.pid
        or version.get("process_start_ticks") != client.process_start_ticks
    ):
        raise DomainError(
            503,
            "server_identity_mismatch",
            "Running server identity does not match its runtime descriptor; refusing process control",
        )
    return client


def _stop_running_server(
    settings: Settings,
    *,
    timeout: float,
    retain_lock: bool = False,
) -> tuple[dict[str, Any], ApplicationLock | None]:
    """Gracefully stop a verified server and optionally retain its writer lock."""

    lock = ApplicationLock(settings.lock_path)
    if lock.acquire():
        settings.runtime_descriptor.unlink(missing_ok=True)
        result = {"stopped": False, "already_stopped": True, "pid": None, "port": settings.port}
        if retain_lock:
            return result, lock
        lock.release()
        return result, None

    client = _verified_client(
        settings,
        missing_code="server_stop_unavailable",
        missing_message=(
            "The lock is held, but no verified current Research Monitor server is available. "
            "If it was started by an older release, stop it once with Ctrl+C in its original terminal."
        ),
    )
    response = client.request(
        "POST",
        "/api/v1/server/stop",
        json_body={"instance_id": client.instance_id},
    )
    if (
        not isinstance(response, dict)
        or response.get("stopping") is not True
        or response.get("instance_id") != client.instance_id
        or response.get("pid") != client.pid
    ):
        raise DomainError(
            503,
            "invalid_stop_response",
            "Running server returned an invalid shutdown acknowledgement",
        )

    deadline = time.monotonic() + timeout
    lock = ApplicationLock(settings.lock_path)
    while not lock.acquire():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DomainError(
                503,
                "server_stop_timeout",
                f"Research Monitor did not stop gracefully within {timeout:g} seconds",
            )
        time.sleep(min(0.05, remaining))

    settings.runtime_descriptor.unlink(missing_ok=True)
    result = {
        "stopped": True,
        "already_stopped": False,
        "pid": client.pid,
        "port": urlparse(client.base_url).port or settings.port,
    }
    if retain_lock:
        return result, lock
    lock.release()
    return result, None


def _coordinated(
    local: Callable[[Any, AppService, Any], Any],
    *,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    write: bool = False,
) -> Any:
    """Use this host's server, or hold both locks for in-process access."""

    settings = Settings.load()
    locks, blocked_by, owner = _try_data_access_locks(settings)
    if locks is not None:
        try:
            # Both locks prove that a host-local descriptor is stale and no
            # other host can access the shared database concurrently.
            settings.runtime_descriptor.unlink(missing_ok=True)
            try:
                database = get_database(settings)
            except DatabaseIntegrityError as exc:
                raise DomainError(
                    503,
                    "database_integrity_failed",
                    str(exc),
                    {"path": str(exc.path), "result": exc.result},
                ) from exc
            except DatabaseSchemaError as exc:
                raise DomainError(
                    409,
                    "database_schema_invalid",
                    str(exc),
                    {"path": str(exc.path), "detail": exc.detail},
                ) from exc
            except DatabaseCompatibilityError as exc:
                raise DomainError(
                    409,
                    "schema_incompatible",
                    str(exc),
                    {"found": exc.found, "expected": exc.expected},
                ) from exc
            service = AppService(settings)
            context = database.write_session() if write else database.session()
            with context as session:
                return local(database, service, session)
        finally:
            locks.release()

    if blocked_by == "shared":
        raise _shared_writer_error(settings, owner)
    client = _verified_client(settings)
    return client.request(method, path, params=params, json_body=json_body)


def _read_json(path: str) -> dict[str, Any]:
    if path == "-": return json.load(sys.stdin)
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


@app.command("version")
def version(json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON")) -> None:
    del json_output
    _print(_envelope({"version": __version__, "api_version": API_VERSION, "schema_version": SCHEMA_VERSION}))


@app.command("stop")
def stop(
    timeout: float = typer.Option(
        10.0,
        "--timeout",
        min=0.1,
        max=60.0,
        help="Seconds to wait for a graceful shutdown",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Gracefully stop the verified local Research Monitor server."""

    try:
        result, _lock = _stop_running_server(Settings.load(), timeout=timeout)
        if json_output:
            _print(_envelope(result))
        elif result["already_stopped"]:
            typer.echo("Research Monitor is not running.")
        else:
            typer.echo(f"Stopped Research Monitor process {result['pid']}.")
    except DomainError as exc:
        _print(_envelope(error=exc.as_detail()))
        raise typer.Exit(_exit_for(exc)) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print(_envelope(error={"code": "invalid_input", "message": str(exc)}))
        raise typer.Exit(2) from exc


@app.command("serve")
def serve(
    port: int | None = typer.Option(None, help="Local TCP port"),
    open_browser: bool = typer.Option(False, "--open", help="Open the dashboard in a browser"),
    force_restart: bool = typer.Option(
        False,
        "--force-restart",
        help="Gracefully stop a verified running monitor before starting",
    ),
    restart_timeout: float = typer.Option(
        10.0,
        "--restart-timeout",
        min=0.1,
        max=60.0,
        help="Seconds to wait for the running monitor to stop",
    ),
) -> None:
    import uvicorn
    from .api import create_app

    settings = Settings.load()
    selected_port = port or settings.port
    locks, blocked_by, owner = _try_data_access_locks(settings)
    if locks is None and blocked_by == "local":
        if not force_restart:
            _print(_envelope(error={
                "code": "already_running",
                "message": (
                    "Research Monitor is already running. Use `research-monitor open`, "
                    "`research-monitor stop`, or `research-monitor serve --force-restart`."
                ),
            }))
            raise typer.Exit(6)
        try:
            stopped, retained_lock = _stop_running_server(
                settings, timeout=restart_timeout, retain_lock=True,
            )
        except DomainError as exc:
            _print(_envelope(error=exc.as_detail()))
            raise typer.Exit(_exit_for(exc)) from exc
        if retained_lock is None:
            _print(_envelope(error={
                "code": "restart_lock_lost",
                "message": "Could not retain the host-local application lock for restart",
            }))
            raise typer.Exit(6)
        locks, blocked_by, owner = _try_data_access_locks(
            settings,
            retained_local=retained_lock,
        )
        if port is None:
            selected_port = int(stopped["port"])
        if stopped["stopped"]:
            typer.echo(f"Stopped previous Research Monitor process {stopped['pid']}.")

    if locks is None:
        if blocked_by == "shared":
            error = _shared_writer_error(settings, owner)
            _print(_envelope(error=error.as_detail()))
            raise typer.Exit(_exit_for(error))
        _print(_envelope(error={
            "code": "lock_unavailable",
            "message": "Could not acquire Research Monitor data-access locks",
        }))
        raise typer.Exit(6)

    bootstrap_token = secrets.token_urlsafe(32)
    server_instance_id = secrets.token_urlsafe(24)
    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True

    try:
        # Both locks prove that a descriptor from an earlier local process is stale.
        settings.runtime_descriptor.unlink(missing_ok=True)
        server_start_ticks = process_start_ticks(os.getpid())
        if server_start_ticks is None:
            _print(_envelope(error={
                "code": "process_identity_unavailable",
                "message": "Linux process identity is unavailable; refusing to publish an unsafe runtime descriptor",
            }))
            raise typer.Exit(6)

        # Validate and initialize the database before publishing a discoverable
        # runtime descriptor or telling the user that the browser URL is ready.
        application = create_app(
            settings=settings,
            browser_bootstrap_token=bootstrap_token,
            server_instance_id=server_instance_id,
            shutdown_callback=request_shutdown,
        )
        browser_url = f"http://{settings.host}:{selected_port}/__bootstrap/{bootstrap_token}"
        settings.write_runtime_descriptor(
            selected_port,
            instance_id=server_instance_id,
            process_start_ticks=server_start_ticks,
            browser_url=browser_url,
        )
        typer.echo(f"Browser URL: {browser_url}")
        if open_browser:
            threading.Timer(0.8, lambda: webbrowser.open(browser_url)).start()
        server = uvicorn.Server(uvicorn.Config(
            application,
            host=settings.host,
            port=selected_port,
            log_level="info",
        ))
        server.run()
    except DatabaseIntegrityError as exc:
        _print(_envelope(error=_database_integrity_error(exc)))
        raise typer.Exit(6) from exc
    except DatabaseSchemaError as exc:
        _print(_envelope(error=_database_schema_error(exc)))
        raise typer.Exit(5) from exc
    except DatabaseCompatibilityError as exc:
        _print(_envelope(error={
            "code": "schema_incompatible",
            "message": str(exc),
            "details": {"found": exc.found, "expected": exc.expected},
        }))
        raise typer.Exit(5) from exc
    finally:
        try:
            settings.runtime_descriptor.unlink(missing_ok=True)
        finally:
            locks.release()


@app.command("open")
def open_dashboard(
    open_browser: bool = typer.Option(
        True,
        "--open/--no-open",
        help="Open the minted one-use dashboard URL in the default browser",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    """Mint a fresh browser session for an already-running monitor."""

    try:
        settings = Settings.load()
        client = _verified_client(
            settings,
            missing_code="server_unavailable",
            missing_message="Research Monitor is not running; start it with `research-monitor serve`",
        )
        value = client.request("POST", "/api/v1/browser/bootstrap", json_body={})
        if not isinstance(value, dict):
            raise DomainError(
                503, "unsafe_bootstrap_url", "Running server returned an invalid browser URL",
            )
        browser_url = str(value.get("browser_url") or "")
        parsed, expected = urlparse(browser_url), urlparse(client.base_url)
        capability = parsed.path.removeprefix("/__bootstrap/")
        if (
            parsed.scheme != expected.scheme
            or parsed.netloc != expected.netloc
            or not parsed.path.startswith("/__bootstrap/")
            or not capability
            or "/" in capability
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise DomainError(
                503, "unsafe_bootstrap_url", "Running server returned an invalid browser URL",
            )
        result = {
            "browser_url": browser_url,
            "expires_in_seconds": int(value.get("expires_in_seconds") or 0),
        }
        if result["expires_in_seconds"] <= 0:
            raise DomainError(
                503, "unsafe_bootstrap_url", "Running server returned an invalid browser URL",
            )
        if json_output:
            _print(_envelope(result))
        else:
            typer.echo(f"Browser URL: {browser_url}")
            typer.echo(f"Expires in: {result['expires_in_seconds']} seconds")
        if open_browser:
            webbrowser.open(browser_url)
    except DomainError as exc:
        _print(_envelope(error=exc.as_detail()))
        raise typer.Exit(_exit_for(exc)) from exc
    except (OSError, ValueError, TypeError) as exc:
        _print(_envelope(error={"code": "invalid_input", "message": str(exc)}))
        raise typer.Exit(2) from exc


@project_app.command("list")
def project_list(json_output: bool = typer.Option(False, "--json"), include_archived: bool = True) -> None:
    del json_output
    def run() -> Any:
        return _coordinated(
            lambda _database, service, session: {"projects": service.list_projects(session, include_archived)},
            method="GET", path="/api/v1/projects", params={"include_archived": include_archived},
        )
    _invoke(run)


@project_app.command("add")
def project_add(path: str, name: str | None = None, json_output: bool = typer.Option(False, "--json")) -> None:
    del json_output
    def run() -> Any:
        root = Path(path).expanduser().resolve()
        payload = ProjectCreate(name=name or root.name, root_path=str(root))
        return _coordinated(
            lambda _database, service, session: {"project": service.create_project(session, payload)},
            method="POST", path="/api/v1/projects", json_body=payload.model_dump(mode="json"), write=True,
        )
    _invoke(run)


@project_app.command("resolve")
def project_resolve(path: str = typer.Option(..., "--path"), json_output: bool = typer.Option(False, "--json")) -> None:
    del json_output
    def run() -> Any:
        resolved = str(Path(path).expanduser().resolve())
        return _coordinated(
            lambda _database, service, session: service.resolve_project(session, resolved),
            method="GET", path="/api/v1/projects/resolve", params={"path": resolved},
        )
    _invoke(run)


@project_app.command("purge")
def project_purge(
    project_id: str,
    confirm: str = typer.Option("", "--confirm", help="Repeat the exact project UUID"),
) -> None:
    def run() -> Any:
        settings = Settings.load()
        locks = _require_stopped_data_access(
            settings,
            local_message="Stop Research Monitor before permanently purging a project",
        )
        try:
            settings.runtime_descriptor.unlink(missing_ok=True)
            return purge_project(get_database(settings), project_id, confirm=confirm)
        finally:
            locks.release()
    _invoke(run)


@agent_app.command("context")
def agent_context(project: str = typer.Option(..., "--project"), json_output: bool = typer.Option(True, "--json/--no-json")) -> None:
    del json_output
    def run() -> Any:
        return _coordinated(
            lambda _database, service, session: service.agent_context(session, project),
            method="GET", path=f"/api/v1/projects/{project}/agent-context",
        )
    _invoke(run)


@proposal_app.command("validate")
def proposal_validate(project: str = typer.Option(..., "--project"), file: str = typer.Option("-", "--file")) -> None:
    correlation: dict[str, str | None] = {"request_id": None}
    def run() -> Any:
        raw = _read_json(file)
        try: correlation["request_id"] = str(UUID(str(raw.get("request_id"))))
        except (TypeError, ValueError): pass
        if raw.get("project_id") not in (None, project):
            raise DomainError(422, "project_mismatch", "Proposal project_id does not match --project")
        raw["project_id"] = project
        payload = ProposalEnvelope.model_validate(raw)
        return _coordinated(
            lambda _database, service, session: service.validate_proposal(session, project, payload),
            method="POST", path=f"/api/v1/projects/{project}/proposals/validate",
            json_body=payload.model_dump(mode="json"), write=True,
        )
    _invoke(run, request_id=lambda: correlation["request_id"])


@proposal_app.command("create")
def proposal_create(project: str = typer.Option(..., "--project"), file: str = typer.Option("-", "--file")) -> None:
    correlation: dict[str, str | None] = {"request_id": None}
    def run() -> Any:
        raw = _read_json(file)
        try: correlation["request_id"] = str(UUID(str(raw.get("request_id"))))
        except (TypeError, ValueError): pass
        if raw.get("project_id") not in (None, project):
            raise DomainError(422, "project_mismatch", "Proposal project_id does not match --project")
        raw["project_id"] = project
        payload = ProposalEnvelope.model_validate(raw)
        return _coordinated(
            lambda _database, service, session: service.create_proposal(session, project, payload),
            method="POST", path=f"/api/v1/projects/{project}/proposals",
            json_body=payload.model_dump(mode="json"), write=True,
        )
    _invoke(run, request_id=lambda: correlation["request_id"])


@proposal_app.command("inspect")
def proposal_inspect(proposal_id: str, json_output: bool = typer.Option(False, "--json")) -> None:
    del json_output
    def run() -> Any:
        return _coordinated(
            lambda _database, service, session: service.proposal(session, proposal_id),
            method="GET", path=f"/api/v1/proposals/{proposal_id}",
        )
    _invoke(run)


def _atomic_private_text_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


@export_app.command("project")
def export_project(project: str = typer.Option(..., "--project"), output: Path | None = typer.Option(None, "--output")) -> None:
    try:
        target = output.expanduser().resolve() if output else None
        value = _coordinated(
            lambda database, service, session: (
                validate_monitor_output_target(database, target, purpose="export")
                if target is not None else None,
                service.export_project(session, project),
            )[1],
            method="GET", path=f"/api/v1/projects/{project}/export",
            params={"output_path": str(target)} if target is not None else None,
        )
        rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if target is not None:
            _atomic_private_text_write(target, rendered)
            _print(_envelope({"path": str(target)}))
        else: typer.echo(rendered, nl=False)
    except DomainError as exc:
        _print(_envelope(error=exc.as_detail())); raise typer.Exit(_exit_for(exc)) from exc


@backup_app.command("create")
def backup_create(
    output: Path | None = typer.Option(None, "--output"),
    force: bool = typer.Option(False, "--force", help="Replace an existing custom target"),
) -> None:
    def run() -> Any:
        settings = Settings.load()
        requested = output.expanduser().resolve() if output else None
        locks, blocked_by, owner = _try_data_access_locks(settings)
        if locks is None:
            if blocked_by == "shared":
                raise _shared_writer_error(settings, owner)
            client = _verified_client(settings)
            return client.request(
                "POST",
                "/api/v1/backup",
                json_body={
                    "output": str(requested) if requested else None,
                    "force": force,
                },
            )
        try:
            settings.runtime_descriptor.unlink(missing_ok=True)
            if settings.database_path.exists():
                if not settings.database_path.is_file():
                    raise DomainError(
                        422,
                        "invalid_database_path",
                        "Research Monitor database path is not a regular file",
                    )
                # Backup is the recovery path for databases that cannot
                # initialize. Deliberately avoid get_database(), which runs
                # migrations before create_backup can preserve the input.
                database = Database(settings.database_path)
                try:
                    path = create_backup(database, requested, force=force)
                finally:
                    database.engine.dispose()
            else:
                # Preserve fresh-home behavior by initializing a complete empty
                # monitor before its first backup.
                database = get_database(settings)
                path = create_backup(database, requested, force=force)
            return {"path": str(path), "integrity": "ok"}
        finally:
            locks.release()
    _invoke(run)


@backup_app.command("restore")
def backup_restore(path: Path, confirm: bool = typer.Option(False, "--confirm")) -> None:
    def run() -> Any:
        settings = Settings.load()
        locks = _require_stopped_data_access(
            settings,
            local_message="Stop Research Monitor before restoring",
        )
        database: Database | None = None
        try:
            settings.runtime_descriptor.unlink(missing_ok=True)
            # Restore must not initialize the database it is intended to replace:
            # corruption is one of the primary reasons this command exists.
            reset_database_singleton()
            database = Database(settings.database_path)
            return restore_backup(database, path, confirm=confirm)
        finally:
            if database is not None:
                database.engine.dispose()
            reset_database_singleton()
            locks.release()

    _invoke(run)


def _skill_source() -> Path:
    override = os.environ.get("RESEARCH_MONITOR_SKILL_SOURCE")
    candidates = [Path(override)] if override else []
    candidates.extend([Path(__file__).resolve().parents[2] / "skills" / "research-monitor", Path(__file__).resolve().parent / "bundled_skill"])
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "SKILL.md").is_file(): return candidate
    raise DomainError(404, "skill_bundle_missing", "Bundled research-monitor skill was not found")


def _skill_destination() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser() / "skills" / "research-monitor"


def _skill_state_path(destination: Path | None = None) -> Path:
    target = destination or _skill_destination()
    return target.parent / ".research-monitor-install.json"


def _tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(value for value in path.rglob("*") if value.is_file()):
        digest.update(str(item.relative_to(path)).encode()); digest.update(item.read_bytes())
    return digest.hexdigest()


def _installed_skill_baseline(destination: Path) -> str | None:
    try:
        value = json.loads(_skill_state_path(destination).read_text(encoding="utf-8"))
        baseline = str(value["installed_hash"])
        if len(baseline) == 64 and all(character in "0123456789abcdef" for character in baseline):
            return baseline
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return None


def _validate_skill_tree(path: Path) -> None:
    try:
        validate_skill_tree(path)
    except SkillBundleValidationError as exc:
        raise DomainError(422, "skill_validation_failed", str(exc)) from exc


@skill_app.command("status")
def skill_status() -> None:
    def run() -> Any:
        source = _skill_source(); destination = _skill_destination()
        source_hash = _tree_hash(source)
        installed_hash = _tree_hash(destination) if destination.is_dir() else None
        baseline = _installed_skill_baseline(destination)
        modified = bool(installed_hash and installed_hash != (baseline or source_hash))
        return {"installed": destination.is_dir(), "modified": modified, "update_available": bool(installed_hash and installed_hash != source_hash), "source_hash": source_hash, "installed_hash": installed_hash, "baseline_hash": baseline, "path": str(destination)}
    _invoke(run)


def _install_skill(force: bool) -> dict[str, Any]:
    source = _skill_source(); destination = _skill_destination(); destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = _tree_hash(source)
    installed_hash = _tree_hash(destination) if destination.exists() else None
    baseline = _installed_skill_baseline(destination)
    modified = bool(installed_hash and installed_hash != (baseline or source_hash))
    if modified and not force: raise DomainError(409, "skill_modified", "Installed skill has local modifications; rerun with --force to back it up and replace it")
    staging_root = Path(tempfile.mkdtemp(prefix="research-monitor-skill-", dir=destination.parent))
    staging = staging_root / "research-monitor"
    backup = None
    try:
        shutil.copytree(source, staging)
        _validate_skill_tree(staging)
        if destination.exists() and modified:
            backup = destination.with_name(f"research-monitor.backup-{installed_hash[:10]}")
            if backup.exists(): shutil.rmtree(backup)
            shutil.copytree(destination, backup)
        previous = destination.with_name("research-monitor.previous")
        if previous.exists(): shutil.rmtree(previous)
        state_path = _skill_state_path(destination)
        staged_state = staging_root / "install-state.json"
        staged_state.write_text(json.dumps({"installed_hash": source_hash, "schema_version": 1}) + "\n", encoding="utf-8")
        staged_state.chmod(0o600)
        if destination.exists(): os.replace(destination, previous)
        try:
            os.replace(staging, destination)
            os.replace(staged_state, state_path)
        except Exception:
            if destination.exists(): shutil.rmtree(destination)
            if previous.exists(): os.replace(previous, destination)
            raise
        if previous.exists(): shutil.rmtree(previous)
        return {"path": str(destination), "hash": _tree_hash(destination), "backup": str(backup) if backup else None, "modified_install_replaced": modified}
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


@skill_app.command("install")
def skill_install(force: bool = typer.Option(False, "--force")) -> None: _invoke(lambda: _install_skill(force))


@skill_app.command("update")
def skill_update(force: bool = typer.Option(False, "--force")) -> None: _invoke(lambda: _install_skill(force))


def main() -> None:
    """Run the CLI while keeping parser failures inside the JSON contract."""

    command = get_command(app)
    args = sys.argv[1:] or ["--help"]
    try:
        result = command.main(
            args=args,
            prog_name="research-monitor",
            standalone_mode=False,
        )
    except typer_click.exceptions.UsageError as exc:
        _print(
            _envelope(
                error={
                    "code": "invalid_input",
                    "message": exc.format_message(),
                }
            )
        )
        raise SystemExit(2) from exc
    if isinstance(result, int):
        raise SystemExit(result)


if __name__ == "__main__":
    main()
