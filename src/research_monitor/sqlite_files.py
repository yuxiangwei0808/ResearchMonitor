from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


SQLITE_FILE_SUFFIXES = ("", "-wal", "-shm", "-journal")


def sqlite_file_set(database_path: Path) -> list[Path]:
    """Return the existing main database and sidecars without opening SQLite."""

    database_path = Path(database_path)
    return [
        candidate
        for suffix in SQLITE_FILE_SUFFIXES
        if (candidate := Path(f"{database_path}{suffix}")).exists()
    ]


def fsync_directory(path: Path) -> None:
    """Persist directory-entry changes before reporting atomic publication."""

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_private_file(source: Path, destination: Path) -> dict[str, object]:
    """Publish a byte-for-byte owner-only copy and return verification metadata."""

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        os.fchmod(file_descriptor, 0o600)
        with source.open("rb") as reader, os.fdopen(file_descriptor, "wb") as writer:
            file_descriptor = -1
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o600)
        fsync_directory(destination.parent)
        return {
            "path": str(destination),
            "size_bytes": size_bytes,
            "sha256": digest.hexdigest(),
        }
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)


def write_private_json(path: Path, value: dict[str, object]) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(file_descriptor, 0o600)
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        fsync_directory(path.parent)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        temporary.unlink(missing_ok=True)


def preserve_sqlite_file_set(
    database_path: Path,
    *,
    reason: str,
    stem: str,
) -> dict[str, object]:
    """Preserve an unverified SQLite main/sidecar set without opening SQLite."""

    database_path = Path(database_path)
    sources = sqlite_file_set(database_path)
    if not sources:
        raise FileNotFoundError(
            "No SQLite main database or sidecar files were available to preserve"
        )
    root = database_path.parent / "forensics"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    fsync_directory(root.parent)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = root / f"{stem}-{stamp}-{uuid4().hex[:8]}"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    fsync_directory(root)
    records = [_copy_private_file(source, directory / source.name) for source in sources]
    manifest: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_database_path": str(database_path),
        "reason": reason,
        "files": records,
    }
    manifest_path = directory / "manifest.json"
    write_private_json(manifest_path, manifest)
    return {
        "directory": str(directory),
        "manifest": str(manifest_path),
        "reason": reason,
        "files": records,
    }
