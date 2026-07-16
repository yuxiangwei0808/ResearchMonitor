from __future__ import annotations

import json
import os
import secrets
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path


def process_start_ticks(pid: int) -> int | None:
    """Return Linux's stable process start time from ``/proc/<pid>/stat``.

    A PID by itself can be reused. Pairing it with field 22 from procfs lets
    runtime discovery distinguish the server that wrote the descriptor from a
    later process with the same PID.
    """

    if pid <= 0:
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        closing_paren = stat.rfind(")")
        if closing_paren < 0:
            return None
        fields_after_command = stat[closing_paren + 1 :].split()
        # The first value here is field 3 (state), so field 22 is index 19.
        value = int(fields_after_command[19])
    except (OSError, IndexError, ValueError):
        return None
    return value if value > 0 else None


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


@dataclass(frozen=True)
class Settings:
    home: Path
    data_dir: Path
    config_dir: Path
    runtime_dir: Path
    database_path: Path
    config_path: Path
    runtime_descriptor: Path
    lock_path: Path
    host: str = "127.0.0.1"
    port: int = 8765
    allowed_roots: tuple[Path, ...] = ()
    cli_token: str = ""

    @property
    def shared_lock_path(self) -> Path:
        """Cross-host writer lock stored beside the shared SQLite database."""

        return self.database_path.parent / "writer.lock"

    @classmethod
    def load(cls) -> "Settings":
        override = os.environ.get("RESEARCH_MONITOR_HOME")
        if override:
            home = Path(override).expanduser().resolve()
            data_dir = config_dir = runtime_dir = home
        else:
            user_home = Path.home()
            data_dir = Path(
                os.environ.get("XDG_DATA_HOME", user_home / ".local" / "share")
            ) / "research-monitor"
            config_dir = Path(
                os.environ.get("XDG_CONFIG_HOME", user_home / ".config")
            ) / "research-monitor"
            xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
            runtime_dir = (
                Path(xdg_runtime) / "research-monitor"
                if xdg_runtime
                else data_dir / "runtime"
            )
            home = data_dir

        data_dir = _private_dir(data_dir)
        config_dir = _private_dir(config_dir)
        runtime_dir = _private_dir(runtime_dir)
        config_path = config_dir / "config.toml"
        config: dict = {}
        if config_path.exists():
            try:
                config = tomllib.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                config = {}
        else:
            legacy = config_dir / "config.json"
            if legacy.exists():
                try:
                    config = json.loads(legacy.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    config = {}

        env_roots = os.environ.get("RESEARCH_MONITOR_ALLOWED_ROOTS")
        raw_roots = (
            env_roots.split(os.pathsep)
            if env_roots
            else config.get("allowed_roots", [str(Path.home())])
        )
        allowed_roots = tuple(Path(p).expanduser().resolve() for p in raw_roots if p)
        token_path = runtime_dir / "cli-token"
        token = os.environ.get("RESEARCH_MONITOR_CLI_TOKEN", "")
        if not token:
            if token_path.exists():
                token = token_path.read_text(encoding="utf-8").strip()
            else:
                token = secrets.token_urlsafe(32)
                token_path.write_text(token, encoding="utf-8")
                token_path.chmod(0o600)

        return cls(
            home=home,
            data_dir=data_dir,
            config_dir=config_dir,
            runtime_dir=runtime_dir,
            database_path=data_dir / "monitor.db",
            config_path=config_path,
            runtime_descriptor=runtime_dir / "server.json",
            lock_path=runtime_dir / "app.lock",
            host="127.0.0.1",
            port=int(os.environ.get("RESEARCH_MONITOR_PORT", config.get("port", 8765))),
            allowed_roots=allowed_roots,
            cli_token=token,
        )

    def write_runtime_descriptor(
        self,
        port: int,
        *,
        instance_id: str,
        process_start_ticks: int,
        browser_url: str | None = None,
    ) -> None:
        if not instance_id or process_start_ticks <= 0:
            raise ValueError("Runtime identity must include an instance ID and process start ticks")
        payload = {
            "api_version": "1",
            "host": self.host,
            "port": port,
            "pid": os.getpid(),
            "instance_id": instance_id,
            "process_start_ticks": process_start_ticks,
            "token_path": str(self.runtime_dir / "cli-token"),
        }
        if browser_url is not None:
            payload["browser_url"] = browser_url
        self.runtime_descriptor.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.runtime_descriptor.name}.",
            suffix=".tmp",
            dir=self.runtime_descriptor.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.runtime_descriptor)
            self.runtime_descriptor.chmod(0o600)
        finally:
            if fd >= 0:
                os.close(fd)
            temporary_path.unlink(missing_ok=True)
