from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from .database import Database
from .service import DomainError
from .sqlite_backup import (
    SQLiteBackupIntegrityError,
    create_verified_sqlite_backup,
    open_sqlite_read_only,
)
from .sqlite_files import (
    SQLITE_FILE_SUFFIXES,
    fsync_directory,
    preserve_sqlite_file_set,
    sqlite_file_set,
    write_private_json,
)


def _sqlite_backup_file(source: Path, destination: Path) -> None:
    """Create one SQLite-consistent file copy using the online backup API."""

    create_verified_sqlite_backup(source, destination, replace=True)


def _preserve_forensic_file_set(database: Database, reason: str) -> dict[str, object]:
    """Preserve an unverified SQLite file set without asking SQLite to read it."""

    try:
        return preserve_sqlite_file_set(
            database.path,
            reason=reason,
            stem="pre-restore",
        )
    except FileNotFoundError as exc:
        raise DomainError(
            500,
            "forensic_preservation_failed",
            "The current database could not be backed up and no SQLite files were available to preserve",
        ) from exc


def _update_forensic_reason(
    preservation: dict[str, object], reason: str
) -> dict[str, object]:
    manifest_path = Path(str(preservation["manifest"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reason"] = reason
    write_private_json(manifest_path, manifest)
    preservation["reason"] = reason
    return preservation


def _discard_forensic_staging(preservation: dict[str, object]) -> None:
    directory = Path(str(preservation["directory"]))
    root = directory.parent
    shutil.rmtree(directory)
    fsync_directory(root)


def _path_within(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _enrolled_roots(database: Database, *, purpose: str) -> list[Path]:
    """Read every root needed to prove that a custom output target is safe.

    A damaged database must not turn a failed root query into an empty root
    set: that would allow a custom backup/export to be published inside an
    enrolled research directory precisely when the safety check is least
    reliable.
    """

    roots: list[str] = []
    connection: sqlite3.Connection | None = None
    try:
        connection = open_sqlite_read_only(database.path)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required_catalogs = {"projects", "artifact_roots"}
        if not required_catalogs.issubset(tables):
            raise ValueError("the root catalogs are missing")
        roots.extend(row[0] for row in connection.execute("SELECT root_path FROM projects"))
        roots.extend(row[0] for row in connection.execute("SELECT root_path FROM artifact_roots"))
        if any(not isinstance(value, str) or not value for value in roots):
            raise ValueError("a stored root path is missing or invalid")
        return [Path(value).expanduser().resolve() for value in roots]
    except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as exc:
        raise DomainError(
            503,
            f"cannot_validate_{purpose}_target",
            (
                f"Cannot validate the {purpose} target because enrolled and "
                "approved research roots could not be read"
            ),
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def validate_monitor_output_target(
    database: Database,
    output: Path,
    *,
    purpose: str = "output",
    check_enrolled_roots: bool = True,
    allow_unreadable_roots: bool = False,
) -> Path:
    target = Path(output).expanduser().resolve()
    database_path = database.path.expanduser().resolve()
    reserved = {
        database_path,
        Path(f"{database_path}-wal").resolve(),
        Path(f"{database_path}-shm").resolve(),
        Path(f"{database_path}-journal").resolve(),
    }
    if target in reserved:
        raise DomainError(
            422, f"unsafe_{purpose}_target",
            f"{purpose.capitalize()} output cannot replace the live database or its SQLite sidecars",
        )
    roots: list[Path] = []
    if check_enrolled_roots:
        try:
            roots = _enrolled_roots(database, purpose=purpose)
        except DomainError:
            if not allow_unreadable_roots:
                raise
    if any(_path_within(target, root) for root in roots):
        raise DomainError(
            422, f"{purpose}_target_in_project",
            f"{purpose.capitalize()} output cannot be written inside an enrolled or approved research root",
        )
    return target


def create_backup(
    database: Database, output: Path | None = None, *, force: bool = False
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = output or database.path.parent / "backups" / f"monitor-{stamp}.db"
    target = validate_monitor_output_target(
        database,
        Path(target),
        purpose="backup",
        # A healthy database still proves the managed directory is outside
        # every enrolled root. Only the monitor-owned recovery destination may
        # proceed when corruption makes that proof unavailable; every
        # caller-selected location fails closed.
        allow_unreadable_roots=output is None,
    )
    if target.exists() and not force:
        raise DomainError(
            409, "backup_target_exists",
            "Backup target already exists; pass --force to replace it",
        )
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output is None:
        target.parent.chmod(0o700)
    try:
        return create_verified_sqlite_backup(database.path, target, replace=force)
    except FileExistsError as exc:
        raise DomainError(
            409,
            "backup_target_exists",
            "Backup target already exists; pass --force to replace it",
        ) from exc
    except SQLiteBackupIntegrityError as exc:
        raise DomainError(
            500,
            "backup_integrity_failed",
            "Backup failed SQLite integrity check",
        ) from exc
    except sqlite3.Error as exc:
        raise DomainError(
            500,
            "backup_integrity_failed",
            "Backup source could not be read as a valid SQLite database",
        ) from exc


def restore_backup(
    database: Database, source: Path, *, confirm: bool = False
) -> dict[str, object]:
    if not confirm:
        raise DomainError(422, "confirmation_required", "Restore requires explicit confirmation")
    if database.path.exists() and not database.path.is_file():
        raise DomainError(
            422,
            "invalid_database_path",
            "Research Monitor database path is not a regular file",
        )
    source = Path(source).expanduser().resolve(strict=True)
    check = open_sqlite_read_only(source)
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
        if not row or row[0] != "ok":
            raise DomainError(422, "invalid_backup", "Backup failed SQLite integrity check")
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "schema_versions" not in tables:
            raise DomainError(422, "invalid_backup", "File is not a Research Monitor database")
        version_row = check.execute("SELECT max(version) FROM schema_versions").fetchone()
        if not version_row or version_row[0] != int(SCHEMA_VERSION):
            raise DomainError(
                409,
                "schema_incompatible",
                f"Backup schema {version_row[0] if version_row else 'unknown'} is incompatible with {SCHEMA_VERSION}",
            )
    finally:
        check.close()

    temporary = database.path.with_suffix(".restore.tmp")
    temporary.unlink(missing_ok=True)
    for suffix in SQLITE_FILE_SUFFIXES[1:]:
        Path(f"{temporary}{suffix}").unlink(missing_ok=True)
    _sqlite_backup_file(source, temporary)
    temporary.chmod(0o600)

    # Initialize and validate an isolated copy before touching the live file.
    candidate = Database(temporary)
    try:
        candidate.initialize()
        if candidate.integrity_check() != "ok":
            raise DomainError(422, "invalid_backup", "Restored copy failed integrity check")
    except Exception as exc:
        candidate.engine.dispose()
        temporary.unlink(missing_ok=True)
        for suffix in SQLITE_FILE_SUFFIXES[1:]:
            Path(f"{temporary}{suffix}").unlink(missing_ok=True)
        if isinstance(exc, DomainError):
            raise
        raise DomainError(
            422,
            "invalid_backup",
            "Backup does not contain a complete compatible Research Monitor schema",
            {"reason": str(exc)},
        ) from exc
    candidate.engine.dispose()

    # Stage a raw copy before asking SQLite to inspect the current file set:
    # even a read-only WAL connection may update shared-memory bookkeeping.
    recovery_backup: Path | None = None
    forensic_preservation: dict[str, object] | None = None
    current_files = sqlite_file_set(database.path)
    staged_forensic = (
        _preserve_forensic_file_set(
            database, "Staged before verified pre-restore backup",
        )
        if current_files
        else None
    )
    if database.path.exists():
        try:
            recovery_backup = create_backup(database)
        except DomainError as exc:
            if exc.code != "backup_integrity_failed":
                if staged_forensic is not None:
                    _discard_forensic_staging(staged_forensic)
                raise
            if staged_forensic is None:
                raise
            forensic_preservation = _update_forensic_reason(
                staged_forensic, str(exc),
            )
        except (OSError, sqlite3.Error) as exc:
            if staged_forensic is None:
                raise
            forensic_preservation = _update_forensic_reason(
                staged_forensic, f"{type(exc).__name__}: {exc}",
            )
        else:
            if staged_forensic is not None:
                _discard_forensic_staging(staged_forensic)
    elif staged_forensic is not None:
        forensic_preservation = _update_forensic_reason(
            staged_forensic,
            "The main database file was missing while SQLite sidecars remained",
        )

    database.engine.dispose()
    try:
        for suffix in SQLITE_FILE_SUFFIXES[1:]:
            Path(f"{database.path}{suffix}").unlink(missing_ok=True)
        os.replace(temporary, database.path)
        for suffix in SQLITE_FILE_SUFFIXES[1:]:
            Path(f"{temporary}{suffix}").unlink(missing_ok=True)
        fsync_directory(database.path.parent)
        database.initialize()
        if database.integrity_check() != "ok":
            raise DomainError(
                500,
                "restore_postcheck_failed",
                "Restored database failed the final SQLite integrity check",
            )
        return {
            "restored_from": str(source),
            "integrity": "ok",
            "verified_pre_restore_backup": (
                str(recovery_backup) if recovery_backup is not None else None
            ),
            "forensic_preservation": forensic_preservation,
        }
    except Exception as restore_error:
        database.engine.dispose()
        for suffix in SQLITE_FILE_SUFFIXES[1:]:
            Path(f"{database.path}{suffix}").unlink(missing_ok=True)
        if recovery_backup is None:
            raise DomainError(
                500,
                "restore_postcheck_failed",
                "Restored database failed final validation; the previous unverified file set remains preserved for recovery",
                {
                    "forensic_preservation": forensic_preservation,
                    "reason": str(restore_error),
                },
            ) from restore_error

        # A verified pre-restore database can be recovered automatically.
        rollback = database.path.with_suffix(".rollback.tmp")
        rollback.unlink(missing_ok=True)
        for suffix in SQLITE_FILE_SUFFIXES[1:]:
            Path(f"{rollback}{suffix}").unlink(missing_ok=True)
        try:
            _sqlite_backup_file(recovery_backup, rollback)
            rollback.chmod(0o600)
            os.replace(rollback, database.path)
            fsync_directory(database.path.parent)
            database.initialize()
            if database.integrity_check() != "ok":
                raise RuntimeError("recovered database failed integrity check")
        except Exception as recovery_error:
            database.engine.dispose()
            raise DomainError(
                500,
                "restore_recovery_failed",
                "Restore failed and automatic recovery could not be verified",
                {
                    "recovery_backup": str(recovery_backup),
                    "restore_reason": str(restore_error),
                    "recovery_reason": str(recovery_error),
                },
            ) from recovery_error
        finally:
            rollback.unlink(missing_ok=True)
            for suffix in SQLITE_FILE_SUFFIXES[1:]:
                Path(f"{rollback}{suffix}").unlink(missing_ok=True)
        raise DomainError(
            500,
            "restore_postcheck_failed",
            "Restored database failed final validation; the original database was recovered",
            {
                "recovery_backup": str(recovery_backup),
                "reason": str(restore_error),
            },
        ) from restore_error
