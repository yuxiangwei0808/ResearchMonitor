from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from research_monitor.api import create_app
from research_monitor.cli import _shared_writer_error, _try_data_access_locks
from research_monitor.config import process_start_ticks
from research_monitor.locking import ApplicationLock
from research_monitor.transport import RuntimeClient


def _cli_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_runtime_descriptor_is_atomic_private_and_process_bound(settings) -> None:
    start_ticks = process_start_ticks(os.getpid())
    assert start_ticks is not None

    settings.write_runtime_descriptor(
        9137,
        instance_id="runtime-instance",
        process_start_ticks=start_ticks,
        browser_url="http://127.0.0.1:9137/__bootstrap/token",
    )

    descriptor = json.loads(settings.runtime_descriptor.read_text(encoding="utf-8"))
    assert descriptor == {
        "api_version": "1",
        "host": "127.0.0.1",
        "port": 9137,
        "pid": os.getpid(),
        "instance_id": "runtime-instance",
        "process_start_ticks": start_ticks,
        "token_path": str(settings.runtime_dir / "cli-token"),
        "browser_url": "http://127.0.0.1:9137/__bootstrap/token",
    }
    assert settings.runtime_descriptor.stat().st_mode & 0o777 == 0o600
    assert not list(settings.runtime_dir.glob(".server.json.*.tmp"))

    client = RuntimeClient.discover(settings)
    assert client == RuntimeClient(
        base_url="http://127.0.0.1:9137",
        token=settings.cli_token,
        pid=os.getpid(),
        instance_id="runtime-instance",
        process_start_ticks=start_ticks,
    )

    descriptor["process_start_ticks"] = start_ticks + 1
    settings.runtime_descriptor.write_text(json.dumps(descriptor), encoding="utf-8")
    assert RuntimeClient.discover(settings) is None


def test_legacy_or_incomplete_runtime_descriptor_is_not_discoverable(settings) -> None:
    settings.runtime_descriptor.write_text(
        json.dumps({
            "api_version": "1",
            "host": "127.0.0.1",
            "port": 8765,
            "pid": os.getpid(),
        }),
        encoding="utf-8",
    )
    assert RuntimeClient.discover(settings) is None


def test_version_identity_and_cli_stop_are_bound_to_exact_instance(
    settings, database,
) -> None:
    callbacks: list[str] = []
    app = create_app(
        settings=settings,
        database=database,
        server_instance_id="expected-instance",
        shutdown_callback=lambda: callbacks.append("stopped"),
    )

    with TestClient(app, headers=_cli_headers(settings.cli_token)) as client:
        version = client.get("/api/v1/version")
        assert version.status_code == 200
        assert version.json() == {
            "api_version": "1",
            "schema_version": "1",
            "version": "0.1.0",
            "server_instance_id": "expected-instance",
            "server_pid": os.getpid(),
            "process_start_ticks": process_start_ticks(os.getpid()),
        }

        mismatch = client.post(
            "/api/v1/server/stop",
            json={"instance_id": "different-instance"},
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["detail"]["code"] == "server_instance_mismatch"
        assert callbacks == []

        stopped = client.post(
            "/api/v1/server/stop",
            json={"instance_id": "expected-instance"},
        )
        assert stopped.status_code == 200
        assert stopped.json() == {
            "stopping": True,
            "instance_id": "expected-instance",
            "pid": os.getpid(),
        }
        assert callbacks == ["stopped"]


def test_browser_cannot_stop_server_and_missing_callback_is_reported(
    settings, database,
) -> None:
    app = create_app(
        settings=settings,
        database=database,
        browser_bootstrap_token="browser-capability",
        server_instance_id="browser-test-instance",
    )
    with TestClient(app) as browser:
        assert browser.get(
            "/__bootstrap/browser-capability", follow_redirects=False,
        ).status_code == 303
        browser.headers.update({
            "Origin": "http://testserver",
            "X-CSRF-Token": browser.cookies["research_monitor_csrf"],
        })
        forbidden = browser.post(
            "/api/v1/server/stop",
            json={"instance_id": "browser-test-instance"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["code"] == "cli_auth_required"

    with TestClient(app, headers=_cli_headers(settings.cli_token)) as cli:
        unavailable = cli.post(
            "/api/v1/server/stop",
            json={"instance_id": "browser-test-instance"},
        )
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "server_stop_unavailable"


def test_application_lock_metadata_is_private_bounded_and_overwrites_stale_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "writer.lock"
    path.write_text(
        json.dumps({
            "hostname": "stale-host",
            "pid": 999999,
            "process_start_ticks": 1,
            "acquired_at_utc": "stale",
        }),
        encoding="utf-8",
    )
    path.chmod(0o666)

    owner = ApplicationLock(path)
    assert owner.acquire()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == owner.owner_metadata
    assert on_disk["hostname"] != "stale-host"
    assert on_disk["pid"] == os.getpid()
    assert on_disk["acquired_at_utc"]
    start_ticks = process_start_ticks(os.getpid())
    if start_ticks is not None:
        assert on_disk["process_start_ticks"] == start_ticks
    assert path.stat().st_mode & 0o777 == 0o600

    contender = ApplicationLock(path)
    assert contender.acquire() is False
    assert contender.owner_metadata == on_disk
    owner.release()

    path.write_text(
        json.dumps({"hostname": "old-owner", "pid": 123}),
        encoding="utf-8",
    )
    successor = ApplicationLock(path)
    assert successor.acquire()
    assert successor.owner_metadata["hostname"] != "old-owner"
    assert successor.owner_metadata["pid"] == os.getpid()
    successor.release()

    held = ApplicationLock(path)
    assert held.acquire()
    path.write_text(
        json.dumps({"hostname": "x" * 5000, "pid": 321}),
        encoding="utf-8",
    )
    bounded = ApplicationLock(path)
    assert bounded.acquire() is False
    assert bounded.owner_metadata == {}
    held.release()


def test_distinct_runtime_dirs_contend_on_one_shared_writer_lock(
    settings,
    tmp_path: Path,
) -> None:
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    runtime_a.mkdir()
    runtime_b.mkdir()
    settings_a = replace(
        settings,
        runtime_dir=runtime_a,
        runtime_descriptor=runtime_a / "server.json",
        lock_path=runtime_a / "app.lock",
    )
    settings_b = replace(
        settings,
        runtime_dir=runtime_b,
        runtime_descriptor=runtime_b / "server.json",
        lock_path=runtime_b / "app.lock",
    )
    assert settings_a.shared_lock_path == settings_b.shared_lock_path
    assert settings_a.shared_lock_path.parent == settings.database_path.parent

    held, blocked_by, owner = _try_data_access_locks(settings_a)
    assert held is not None
    assert blocked_by is None
    assert owner == {}

    blocked, blocked_by, owner = _try_data_access_locks(settings_b)
    assert blocked is None
    assert blocked_by == "shared"
    assert owner["hostname"]
    assert owner["pid"] == os.getpid()
    assert len(json.dumps(owner)) < 1024

    error = _shared_writer_error(settings_b, owner)
    assert error.code == "shared_writer_active"
    assert error.status_code == 503
    assert error.details == {
        "lock_path": str(settings_b.shared_lock_path),
        "owner": owner,
    }

    # Shared-lock failure must release the second host's local lock.
    local_probe = ApplicationLock(settings_b.lock_path)
    assert local_probe.acquire()
    local_probe.release()

    held.release()
    acquired_b, blocked_by, owner = _try_data_access_locks(settings_b)
    assert acquired_b is not None
    assert blocked_by is None
    assert owner == {}
    acquired_b.release()
