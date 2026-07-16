from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

import research_monitor.cli as cli_module
from research_monitor.cli import app
from research_monitor.database import reset_database_singleton
from research_monitor.locking import ApplicationLock


runner = CliRunner()


def test_cli_version_enrollment_resolution_and_context(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "monitor-home"; project = tmp_path / "research"; home.mkdir(); project.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()
    version = runner.invoke(app, ["version", "--json"])
    assert version.exit_code == 0, version.output
    assert json.loads(version.output)["data"]["api_version"] == "1"
    added = runner.invoke(app, ["project", "add", str(project), "--json"])
    assert added.exit_code == 0, added.output
    project_id = json.loads(added.output)["data"]["project"]["id"]
    listed = runner.invoke(app, ["project", "list", "--json"])
    assert [item["id"] for item in json.loads(listed.output)["data"]["projects"]] == [project_id]
    resolved = runner.invoke(app, ["project", "resolve", "--path", str(project), "--json"])
    assert resolved.exit_code == 0, resolved.output
    assert json.loads(resolved.output)["data"]["id"] == project_id
    context = runner.invoke(app, ["agent", "context", "--project", project_id, "--json"])
    assert context.exit_code == 0, context.output
    assert json.loads(context.output)["data"]["project"]["id"] == project_id
    reset_database_singleton()


def test_cli_rejects_unenrolled_resolution(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "monitor-home"; project = tmp_path / "research"; home.mkdir(); project.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home)); monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path)); reset_database_singleton()
    response = runner.invoke(app, ["project", "resolve", "--path", str(project), "--json"])
    assert response.exit_code == 3
    assert json.loads(response.output)["error"]["code"] == "project_not_found"
    reset_database_singleton()


def test_cli_proposal_echoes_request_id_and_uses_precise_not_found_exit(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "monitor-home"; project = tmp_path / "research"; home.mkdir(); project.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home)); monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path)); reset_database_singleton()
    added = runner.invoke(app, ["project", "add", str(project), "--json"])
    project_id = json.loads(added.output)["data"]["project"]["id"]
    request_id = str(uuid4())
    payload = {
        "api_version": "1", "schema_version": "1", "request_id": request_id,
        "project_id": project_id, "base_semantic_revision": 0, "summary": "Forbidden",
        "operations": [{"id": str(uuid4()), "type": "project.trash", "data": {}}],
    }
    rejected = runner.invoke(
        app, ["proposal", "validate", "--project", project_id, "--file", "-"],
        input=json.dumps(payload),
    )
    assert rejected.exit_code == 2
    assert json.loads(rejected.output)["request_id"] == request_id
    missing = runner.invoke(app, ["proposal", "inspect", str(uuid4()), "--json"])
    assert missing.exit_code == 2
    assert json.loads(missing.output)["error"]["code"] == "proposal_not_found"
    reset_database_singleton()


def test_cli_export_rejects_project_paths_and_writes_private_atomic_output(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    project = tmp_path / "research"
    home.mkdir(); project.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()
    added = runner.invoke(app, ["project", "add", str(project), "--json"])
    project_id = json.loads(added.output)["data"]["project"]["id"]

    forbidden_path = project / "monitor-export.json"
    forbidden = runner.invoke(app, [
        "export", "project", "--project", project_id, "--output", str(forbidden_path),
    ])
    assert forbidden.exit_code == 2
    assert json.loads(forbidden.output)["error"]["code"] == "export_target_in_project"
    assert not forbidden_path.exists()

    output = tmp_path / "portable.json"
    exported = runner.invoke(app, [
        "export", "project", "--project", project_id, "--output", str(output),
    ])
    assert exported.exit_code == 0, exported.output
    assert output.is_file()
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))["export_kind"] == "research-monitor-project"
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))
    reset_database_singleton()


def test_cli_open_mints_prints_and_opens_validated_loopback_url(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))

    class FakeClient:
        base_url = "http://127.0.0.1:8765"

        def __init__(self) -> None:
            self.calls: list[tuple[str, str, object]] = []

        def request(self, method, path, *, json_body=None, **_kwargs):
            self.calls.append((method, path, json_body))
            return {
                "browser_url": "http://127.0.0.1:8765/__bootstrap/fresh-token",
                "expires_in_seconds": 60,
            }

    fake = FakeClient()
    opened: list[str] = []
    monkeypatch.setattr(
        cli_module, "_verified_client", lambda _settings, **_kwargs: fake,
    )
    monkeypatch.setattr(cli_module.webbrowser, "open", lambda url: opened.append(url))

    human = runner.invoke(app, ["open"])
    assert human.exit_code == 0, human.output
    assert "Browser URL: http://127.0.0.1:8765/__bootstrap/fresh-token" in human.output
    assert "Expires in: 60 seconds" in human.output
    assert opened == ["http://127.0.0.1:8765/__bootstrap/fresh-token"]
    assert fake.calls == [("POST", "/api/v1/browser/bootstrap", {})]

    machine = runner.invoke(app, ["open", "--no-open", "--json"])
    assert machine.exit_code == 0, machine.output
    assert json.loads(machine.output)["data"]["expires_in_seconds"] == 60
    assert len(opened) == 1


def test_cli_open_requires_running_server_and_rejects_nonlocal_url(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    unavailable = runner.invoke(app, ["open", "--no-open"])
    assert unavailable.exit_code == 6
    assert json.loads(unavailable.output)["error"]["code"] == "server_unavailable"

    class UnsafeClient:
        base_url = "http://127.0.0.1:8765"

        @staticmethod
        def request(*_args, **_kwargs):
            return {
                "browser_url": "https://evil.example/__bootstrap/stolen",
                "expires_in_seconds": 60,
            }

    monkeypatch.setattr(
        cli_module, "_verified_client", lambda _settings, **_kwargs: UnsafeClient(),
    )
    unsafe = runner.invoke(app, ["open", "--no-open"])
    assert unsafe.exit_code == 6
    assert json.loads(unsafe.output)["error"]["code"] == "unsafe_bootstrap_url"


def test_cli_stop_is_idempotent_and_clears_a_stale_descriptor(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    descriptor = home / "server.json"
    descriptor.write_text("stale", encoding="utf-8")

    first = runner.invoke(app, ["stop", "--json"])
    assert first.exit_code == 0, first.output
    payload = json.loads(first.output)["data"]
    assert payload["already_stopped"] is True
    assert not descriptor.exists()

    second = runner.invoke(app, ["stop"])
    assert second.exit_code == 0, second.output
    assert "not running" in second.output

def test_cli_stop_requests_verified_shutdown_and_waits_for_lock(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    owner = ApplicationLock(home / "app.lock")
    assert owner.acquire()

    class FakeClient:
        base_url = "http://127.0.0.1:9123"
        pid = 4321
        instance_id = "verified-instance"
        process_start_ticks = 999
        calls: list[tuple[str, str, object]] = []

        def request(self, method, path, *, json_body=None, **_kwargs):
            self.calls.append((method, path, json_body))
            owner.release()
            return {"stopping": True, "instance_id": self.instance_id, "pid": self.pid}

    fake = FakeClient()
    monkeypatch.setattr(cli_module, "_verified_client", lambda _settings, **_kwargs: fake)
    result = runner.invoke(app, ["stop", "--json", "--timeout", "1"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data == {"already_stopped": False, "pid": 4321, "port": 9123, "stopped": True}
    assert fake.calls == [("POST", "/api/v1/server/stop", {"instance_id": "verified-instance"})]


def test_cli_restore_requires_exclusive_lock_and_reopens_restored_state(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    first = tmp_path / "first-project"
    second = tmp_path / "second-project"
    home.mkdir(); first.mkdir(); second.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()

    first_result = runner.invoke(app, ["project", "add", str(first), "--json"])
    assert first_result.exit_code == 0, first_result.output
    first_id = json.loads(first_result.output)["data"]["project"]["id"]
    backup_path = tmp_path / "offline-restore.db"
    created = runner.invoke(app, [
        "backup", "create", "--output", str(backup_path),
    ])
    assert created.exit_code == 0, created.output
    second_result = runner.invoke(app, ["project", "add", str(second), "--json"])
    assert second_result.exit_code == 0, second_result.output

    lock = ApplicationLock(home / "app.lock")
    assert lock.acquire()
    try:
        locked = runner.invoke(app, [
            "backup", "restore", str(backup_path), "--confirm",
        ])
    finally:
        lock.release()
    assert locked.exit_code == 6
    assert json.loads(locked.output)["error"]["code"] == "application_running"

    restored = runner.invoke(app, [
        "backup", "restore", str(backup_path), "--confirm",
    ])
    assert restored.exit_code == 0, restored.output
    listed = runner.invoke(app, ["project", "list", "--json"])
    project_ids = [item["id"] for item in json.loads(listed.output)["data"]["projects"]]
    assert project_ids == [first_id]
    reset_database_singleton()


def test_cli_managed_backup_succeeds_before_incompatible_database_can_initialize(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    source = home / "monitor.db"
    connection = sqlite3.connect(source)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_versions (version INTEGER PRIMARY KEY);
            INSERT INTO schema_versions (version) VALUES (999);
            CREATE TABLE legacy_records (value TEXT NOT NULL);
            INSERT INTO legacy_records (value) VALUES ('preserve me');
            """
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    reset_database_singleton()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "legacy-backup.db"

    rejected = runner.invoke(
        app, ["backup", "create", "--output", str(output)],
    )
    assert rejected.exit_code == 6, rejected.output
    assert json.loads(rejected.output)["error"]["code"] == "cannot_validate_backup_target"
    assert not output.exists()

    created = runner.invoke(app, ["backup", "create"])
    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)["data"]
    assert payload["integrity"] == "ok"
    backup = Path(payload["path"])
    assert backup.parent == home / "backups"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert not Path(f"{source}-wal").exists()
    assert not Path(f"{source}-shm").exists()
    check = sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert check.execute("SELECT version FROM schema_versions").fetchone() == (999,)
        assert check.execute("SELECT value FROM legacy_records").fetchone() == (
            "preserve me",
        )
    finally:
        check.close()
    reset_database_singleton()

def test_cli_custom_backup_fails_closed_when_database_cannot_enumerate_roots(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    project = tmp_path / "research"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()

    added = runner.invoke(app, ["project", "add", str(project), "--json"])
    assert added.exit_code == 0, added.output
    reset_database_singleton()
    database_path = home / "monitor.db"
    database_path.write_bytes(b"not a SQLite database")
    target_parent = project / "must-not-be-created"
    target = target_parent / "monitor-backup.db"

    rejected = runner.invoke(
        app,
        ["backup", "create", "--output", str(target)],
    )

    assert rejected.exit_code == 6, rejected.output
    assert json.loads(rejected.output)["error"]["code"] == "cannot_validate_backup_target"
    assert not target_parent.exists()
    assert not target.exists()
    assert not list(project.rglob(f".{target.name}.*.tmp"))

    managed = runner.invoke(app, ["backup", "create"])
    assert managed.exit_code == 6, managed.output
    assert json.loads(managed.output)["error"] == {
        "code": "backup_integrity_failed",
        "message": "Backup source could not be read as a valid SQLite database",
    }
    assert not list((home / "backups").glob(".*.tmp"))
    reset_database_singleton()


def test_cli_serve_force_restart_retains_the_released_writer_lock(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    reset_database_singleton()
    owner = ApplicationLock(home / "app.lock")
    assert owner.acquire()
    runs: list[int] = []

    def fake_stop(settings, *, timeout, retain_lock=False):
        assert timeout == 1
        assert retain_lock is True
        owner.release()
        retained = ApplicationLock(settings.lock_path)
        assert retained.acquire()
        return (
            {"stopped": True, "already_stopped": False, "pid": 1234, "port": 9456},
            retained,
        )

    monkeypatch.setattr(cli_module, "_stop_running_server", fake_stop)
    monkeypatch.setattr("uvicorn.Server.run", lambda self: runs.append(self.config.port))
    result = runner.invoke(
        app,
        ["serve", "--force-restart", "--restart-timeout", "1", "--port", "9456"],
    )
    assert result.exit_code == 0, result.output
    assert runs == [9456]
    assert not (home / "server.json").exists()
    probe = ApplicationLock(home / "app.lock")
    assert probe.acquire()
    probe.release()
    reset_database_singleton()

def test_cli_restore_corrupt_current_preserves_private_forensic_set(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    project = tmp_path / "research"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()

    added = runner.invoke(app, ["project", "add", str(project), "--json"])
    assert added.exit_code == 0, added.output
    project_id = json.loads(added.output)["data"]["project"]["id"]
    backup_path = tmp_path / "verified-before-corruption.db"
    created = runner.invoke(
        app, ["backup", "create", "--output", str(backup_path)],
    )
    assert created.exit_code == 0, created.output
    reset_database_singleton()

    database_path = home / "monitor.db"
    original_files = {
        "monitor.db": b"damaged-main-database",
        "monitor.db-wal": b"unverified-wal-evidence",
        "monitor.db-shm": b"unverified-shm-evidence",
    }
    for name, content in original_files.items():
        path = home / name
        path.write_bytes(content)
        path.chmod(0o600)

    restored = runner.invoke(
        app, ["backup", "restore", str(backup_path), "--confirm"],
    )
    assert restored.exit_code == 0, restored.output
    data = json.loads(restored.output)["data"]
    assert data["integrity"] == "ok"
    assert data["restored_from"] == str(backup_path)
    assert data["verified_pre_restore_backup"] is None
    forensic = data["forensic_preservation"]
    assert forensic is not None

    directory = Path(forensic["directory"])
    manifest_path = Path(forensic["manifest"])
    assert directory.stat().st_mode & 0o777 == 0o700
    assert manifest_path.stat().st_mode & 0o777 == 0o600
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_database_path"] == str(database_path)
    assert manifest["reason"]
    records = {Path(item["path"]).name: item for item in manifest["files"]}
    assert set(records) == set(original_files)
    for name, content in original_files.items():
        preserved = Path(records[name]["path"])
        assert preserved.read_bytes() == content
        assert preserved.stat().st_mode & 0o777 == 0o600
        assert records[name]["size_bytes"] == len(content)
        assert records[name]["sha256"] == hashlib.sha256(content).hexdigest()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,),
        ).fetchone() == (project_id,)
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()
    reset_database_singleton()


def test_corrupt_database_errors_are_structured_and_serve_releases_runtime_state(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    reset_database_singleton()
    database_path = home / "monitor.db"
    database_path.write_bytes(b"not a SQLite database")

    listed = runner.invoke(app, ["project", "list", "--json"])
    assert listed.exit_code == 6, listed.output
    listed_error = json.loads(listed.output)["error"]
    assert listed_error["code"] == "database_integrity_failed"
    assert listed_error["details"]["path"] == str(database_path)
    assert listed_error["details"]["result"]

    descriptor = home / "server.json"
    descriptor.write_text('{"stale": true}\n', encoding="utf-8")
    served = runner.invoke(app, ["serve", "--port", "9461"])
    assert served.exit_code == 6, served.output
    served_error = json.loads(served.output)["error"]
    assert served_error["code"] == "database_integrity_failed"
    assert served_error["details"]["path"] == str(database_path)
    assert not descriptor.exists()
    probe = ApplicationLock(home / "app.lock")
    assert probe.acquire()
    probe.release()
    reset_database_singleton()



def test_shared_writer_contention_is_structured_and_releases_local_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    reset_database_singleton()

    owner = ApplicationLock(home / "writer.lock")
    assert owner.acquire()
    expected_owner = owner.owner_metadata
    try:
        listed = runner.invoke(app, ["project", "list", "--json"])
        assert listed.exit_code == 6, listed.output
        listed_error = json.loads(listed.output)["error"]
        assert listed_error == {
            "code": "shared_writer_active",
            "message": (
                "Research Monitor data is already in use by another host or process. "
                "Stop that instance before accessing this shared monitor."
            ),
            "details": {
                "lock_path": str(home / "writer.lock"),
                "owner": expected_owner,
            },
        }
        assert not (home / "monitor.db").exists()

        served = runner.invoke(
            app,
            ["serve", "--force-restart", "--port", "9462"],
        )
        assert served.exit_code == 6, served.output
        served_error = json.loads(served.output)["error"]
        assert served_error["code"] == "shared_writer_active"
        assert served_error["details"]["owner"] == expected_owner

        local_probe = ApplicationLock(home / "app.lock")
        assert local_probe.acquire()
        local_probe.release()
    finally:
        owner.release()
        reset_database_singleton()


def test_server_holds_local_and_shared_locks_for_its_lifetime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    reset_database_singleton()
    observed_ports: list[int] = []

    def fake_run(server) -> None:
        observed_ports.append(server.config.port)
        for path in (home / "app.lock", home / "writer.lock"):
            contender = ApplicationLock(path)
            assert contender.acquire() is False
            assert contender.owner_metadata["pid"] == os.getpid()

    monkeypatch.setattr("uvicorn.Server.run", fake_run)
    served = runner.invoke(app, ["serve", "--port", "9463"])
    assert served.exit_code == 0, served.output
    assert observed_ports == [9463]
    assert not (home / "server.json").exists()

    for path in (home / "app.lock", home / "writer.lock"):
        probe = ApplicationLock(path)
        assert probe.acquire()
        probe.release()
    reset_database_singleton()


def test_forged_current_head_schema_errors_are_structured(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    reset_database_singleton()
    database_path = home / "monitor.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_versions (
                version INTEGER PRIMARY KEY,
                applied_at DATETIME NOT NULL
            );
            INSERT INTO schema_versions(version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
            INSERT INTO alembic_version(version_num) VALUES ('0004');
            """
        )
        connection.commit()
    finally:
        connection.close()

    listed = runner.invoke(app, ["project", "list", "--json"])
    assert listed.exit_code == 5, listed.output
    listed_error = json.loads(listed.output)["error"]
    assert listed_error["code"] == "database_schema_invalid"
    assert listed_error["details"]["path"] == str(database_path)
    assert any(
        marker in listed_error["details"]["detail"]
        for marker in ("missing ORM tables", "search trigger set")
    )

    served = runner.invoke(app, ["serve", "--port", "9464"])
    assert served.exit_code == 5, served.output
    served_error = json.loads(served.output)["error"]
    assert served_error["code"] == "database_schema_invalid"
    assert served_error["details"]["path"] == str(database_path)
    assert not (home / "server.json").exists()
    for path in (home / "app.lock", home / "writer.lock"):
        probe = ApplicationLock(path)
        assert probe.acquire()
        probe.release()
    reset_database_singleton()
