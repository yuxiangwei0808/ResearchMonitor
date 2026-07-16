from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from .sqlite_files import fsync_directory


class SQLiteBackupIntegrityError(RuntimeError):
    """A SQLite backup copy did not pass its destination integrity check."""


def open_sqlite_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite file without granting write access to the database."""

    return sqlite3.connect(
        f"{Path(path).expanduser().resolve().as_uri()}?mode=ro",
        uri=True,
    )


def _integrity_result(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"


def sqlite_integrity_check(path: Path) -> str:
    connection = open_sqlite_read_only(path)
    try:
        return _integrity_result(connection)
    finally:
        connection.close()


def create_verified_sqlite_backup(
    source: Path,
    target: Path,
    *,
    replace: bool = False,
) -> Path:
    """Create, verify, and atomically publish a private SQLite backup."""

    source = Path(source).expanduser().resolve()
    target = Path(target).expanduser().resolve()
    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        try:
            os.fchmod(temporary_fd, 0o600)
        finally:
            os.close(temporary_fd)

        source_connection = open_sqlite_read_only(source)
        try:
            destination_connection = sqlite3.connect(str(temporary))
            try:
                source_connection.backup(destination_connection)
                if _integrity_result(destination_connection) != "ok":
                    raise SQLiteBackupIntegrityError(
                        "Backup failed SQLite integrity check"
                    )
            finally:
                destination_connection.close()
        finally:
            source_connection.close()

        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        if replace:
            os.replace(temporary, target)
        else:
            # Source and target are in one directory, so a hard link publishes
            # atomically without replacing a target created by another process.
            os.link(temporary, target)
            temporary.unlink()
        fsync_directory(target.parent)
        return target
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(f"{temporary}{suffix}").unlink(missing_ok=True)
