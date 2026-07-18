from __future__ import annotations

import fcntl
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

from .config import process_start_ticks


_MAX_OWNER_BYTES = 4096
_MAX_HOSTNAME_LENGTH = 255
_MAX_TIMESTAMP_LENGTH = 64
_MAX_PURPOSE_LENGTH = 64


def _bounded_owner_metadata(handle: TextIO) -> dict[str, Any]:
    """Read only known, bounded diagnostic fields from a contended lock file."""

    try:
        handle.seek(0)
        raw = handle.read(_MAX_OWNER_BYTES + 1)
    except (OSError, UnicodeError):
        return {}
    if len(raw.encode("utf-8")) > _MAX_OWNER_BYTES:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}

    owner: dict[str, Any] = {}
    hostname = value.get("hostname")
    if isinstance(hostname, str) and 0 < len(hostname) <= _MAX_HOSTNAME_LENGTH:
        owner["hostname"] = hostname
    pid = value.get("pid")
    if type(pid) is int and pid > 0:
        owner["pid"] = pid
    start_ticks = value.get("process_start_ticks")
    if type(start_ticks) is int and start_ticks > 0:
        owner["process_start_ticks"] = start_ticks
    acquired_at = value.get("acquired_at_utc")
    if isinstance(acquired_at, str) and 0 < len(acquired_at) <= _MAX_TIMESTAMP_LENGTH:
        owner["acquired_at_utc"] = acquired_at
    purpose = value.get("purpose")
    if isinstance(purpose, str) and 0 < len(purpose) <= _MAX_PURPOSE_LENGTH:
        owner["purpose"] = purpose
    return owner


def _current_owner_metadata(purpose: str | None = None) -> dict[str, Any]:
    hostname = socket.gethostname().strip()[:_MAX_HOSTNAME_LENGTH] or "unknown"
    owner: dict[str, Any] = {
        "hostname": hostname,
        "pid": os.getpid(),
        "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    start_ticks = process_start_ticks(os.getpid())
    if start_ticks is not None:
        owner["process_start_ticks"] = start_ticks
    if purpose is not None:
        owner["purpose"] = purpose[:_MAX_PURPOSE_LENGTH]
    return owner


class ApplicationLock:
    """Owner-only advisory lock with bounded cross-host diagnostic metadata."""

    def __init__(self, path: Path, *, purpose: str | None = None):
        self.path = Path(path)
        self.purpose = purpose
        self.handle: TextIO | None = None
        self.owner_metadata: dict[str, Any] = {}

    def acquire(self, blocking: bool = False) -> bool:
        if self.handle is not None:
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "r+", encoding="utf-8", newline="")
        except BaseException:
            os.close(descriptor)
            raise
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            self.owner_metadata = _bounded_owner_metadata(handle)
            handle.close()
            return False

        owner = _current_owner_metadata(self.purpose)
        try:
            handle.seek(0)
            handle.truncate()
            json.dump(owner, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            raise

        self.handle = handle
        self.owner_metadata = owner
        return True

    def release(self) -> None:
        if self.handle is not None:
            handle = self.handle
            self.handle = None
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def __enter__(self) -> "ApplicationLock":
        if not self.acquire():
            raise RuntimeError("Research Monitor is already running")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
