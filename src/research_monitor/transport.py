from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import Settings, process_start_ticks
from .service import DomainError


@dataclass(frozen=True)
class RuntimeClient:
    base_url: str
    token: str
    pid: int
    instance_id: str
    process_start_ticks: int

    @classmethod
    def discover(cls, settings: Settings) -> "RuntimeClient | None":
        descriptor_path = settings.runtime_descriptor
        if not descriptor_path.is_file():
            return None
        try:
            descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
            host = str(descriptor["host"])
            port = int(descriptor["port"])
            pid = int(descriptor["pid"])
            instance_id = descriptor["instance_id"]
            expected_start_ticks = int(descriptor["process_start_ticks"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        if not isinstance(instance_id, str):
            return None
        if (
            host != "127.0.0.1"
            or not (1 <= port <= 65535)
            or pid <= 0
            or not instance_id
            or expected_start_ticks <= 0
        ):
            raise DomainError(503, "unsafe_runtime_descriptor", "Runtime descriptor is not a safe loopback endpoint")
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, ValueError):
            return None
        except PermissionError:
            # A descriptor owned by this user should never name an inaccessible PID.
            raise DomainError(503, "unsafe_runtime_descriptor", "Runtime descriptor process cannot be verified")
        actual_start_ticks = process_start_ticks(pid)
        if actual_start_ticks is None or actual_start_ticks != expected_start_ticks:
            return None
        return cls(
            base_url=f"http://{host}:{port}",
            token=settings.cli_token,
            pid=pid,
            instance_id=instance_id,
            process_start_ticks=expected_start_ticks,
        )

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any = None) -> Any:
        try:
            response = httpx.request(
                method, f"{self.base_url}{path}", params=params, json=json_body,
                headers={"User-Agent": "research-monitor-cli/0.1", "Authorization": f"Bearer {self.token}", "Accept": "application/json"},
                timeout=10,
            )
        except httpx.HTTPError as exc:
            raise DomainError(503, "server_unavailable", "Running Research Monitor server could not be reached") from exc
        if response.is_error:
            try:
                body = response.json(); detail = body.get("detail", body)
                if isinstance(detail, dict):
                    raise DomainError(response.status_code, str(detail.get("code", "server_error")), str(detail.get("message", response.reason_phrase)), detail.get("details"))
            except (ValueError, AttributeError):
                pass
            raise DomainError(response.status_code, "server_error", response.text or response.reason_phrase)
        if response.status_code == 204:
            return None
        return response.json()
