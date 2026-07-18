from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine

import research_monitor.database as database_module
from research_monitor.database import Database, DatabaseIntegrityError, DatabaseSchemaError
from research_monitor.migrations.schema_v0001 import V0001_METADATA


def _create_delete_mode_legacy_database(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path}", future=True)
    V0001_METADATA.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            V0001_METADATA.tables["schema_versions"].insert(),
            {"version": 1, "applied_at": datetime.now(timezone.utc)},
        )
    engine.dispose()
    assert _journal_mode(path) == "delete"
    assert not _sidecars(path)


def _journal_mode(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).casefold() if row else "unknown"
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sidecars(path: Path) -> list[Path]:
    return [
        candidate
        for candidate in (
            Path(f"{path}-journal"),
            Path(f"{path}-wal"),
            Path(f"{path}-shm"),
        )
        if candidate.exists()
    ]


def test_initialize_preserves_first_backup_before_enforcing_durable_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "delete-mode-legacy.db"
    _create_delete_mode_legacy_database(path)
    source_hash = _sha256(path)
    source_mtime = path.stat().st_mtime_ns

    database = Database(path)
    real_backup = database._verified_pre_migration_backup
    revisions: list[str] = []

    def observe_source_before_backup(revision: str) -> Path:
        if not revisions:
            assert revision == "0001"
            assert _sha256(path) == source_hash
            assert path.stat().st_mtime_ns == source_mtime
            assert _journal_mode(path) == "delete"
            assert not _sidecars(path)
        revisions.append(revision)
        return real_backup(revision)

    monkeypatch.setattr(
        database,
        "_verified_pre_migration_backup",
        observe_source_before_backup,
    )
    try:
        database.initialize()
    finally:
        database.engine.dispose()

    assert revisions == ["0001", "0002", "0003", "0004", "0005"]
    assert len(list((tmp_path / "backups").glob("pre-migration-*.db"))) == 5
    assert _journal_mode(path) == "delete"


def test_initialize_converts_wal_only_after_verified_legacy_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "wal-mode-legacy.db"
    _create_delete_mode_legacy_database(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    finally:
        connection.close()
    assert _journal_mode(path) == "wal"

    database = Database(path)
    real_backup = database._verified_pre_migration_backup
    observed_modes: list[str] = []

    def observe_source_before_backup(revision: str) -> Path:
        observed_modes.append(_journal_mode(path))
        return real_backup(revision)

    monkeypatch.setattr(
        database,
        "_verified_pre_migration_backup",
        observe_source_before_backup,
    )
    try:
        database.initialize()
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
    finally:
        database.engine.dispose()

    assert observed_modes == ["wal", "delete", "delete", "delete", "delete"]
    assert _journal_mode(path) == "delete"
    assert not _sidecars(path)


def test_current_wal_database_is_backed_up_before_journal_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "current-wal.db"
    initialized = Database(path)
    initialized.initialize()
    initialized.engine.dispose()

    wal_code = r"""
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
connection.execute("PRAGMA wal_autocheckpoint=0")
connection.execute(
    "UPDATE schema_versions SET applied_at = '2042-03-04 05:06:07.000000'"
)
connection.commit()
os._exit(0)
"""
    written = subprocess.run(
        [sys.executable, "-c", wal_code, str(path)],
        check=False,
        timeout=30,
    )
    assert written.returncode == 0
    assert Path(f"{path}-wal").is_file()
    assert Path(f"{path}-shm").is_file()

    database = Database(path)
    real_backup = database._verified_pre_journal_change_backup
    backups: list[Path] = []

    def observe_backup() -> Path:
        assert _journal_mode(path) == "wal"
        backup = real_backup()
        check = sqlite3.connect(backup)
        try:
            assert check.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert check.execute(
                "SELECT applied_at FROM schema_versions"
            ).fetchone() == ("2042-03-04 05:06:07.000000",)
        finally:
            check.close()
        backups.append(backup)
        return backup

    monkeypatch.setattr(
        database,
        "_verified_pre_journal_change_backup",
        observe_backup,
    )
    try:
        database.initialize()
    finally:
        database.engine.dispose()

    assert len(backups) == 1
    assert backups[0].name.startswith("pre-journal-change-")
    assert _journal_mode(path) == "delete"
    assert not _sidecars(path)
    check = sqlite3.connect(path)
    try:
        assert check.execute(
            "SELECT applied_at FROM schema_versions"
        ).fetchone() == ("2042-03-04 05:06:07.000000",)
    finally:
        check.close()
    assert not list((tmp_path / "backups").glob("pre-migration-*.db"))


def test_initialize_rejects_corrupt_existing_database_before_engine_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a SQLite database")
    source_hash = _sha256(path)
    database = Database(path)
    normal_access_attempted = False

    def reject_normal_access() -> int | None:
        nonlocal normal_access_attempted
        normal_access_attempted = True
        return None

    monkeypatch.setattr(database, "_declared_schema_version", reject_normal_access)
    try:
        with pytest.raises(DatabaseIntegrityError) as error:
            database.initialize()
    finally:
        database.engine.dispose()

    message = str(error.value)
    assert error.value.path == path
    assert error.value.result
    assert str(path) in message
    assert "refusing to write" in message
    assert "restore a verified backup" in message
    assert normal_access_attempted is False
    assert _sha256(path) == source_hash
    assert not _sidecars(path)


def test_initialized_connections_use_delete_journal_and_full_synchronous(
    tmp_path: Path,
) -> None:
    path = tmp_path / "durable.db"
    database = Database(path)
    try:
        database.initialize()
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
    finally:
        database.engine.dispose()


def test_failed_pre_migration_integrity_check_leaves_delete_database_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "integrity-failure-legacy.db"
    _create_delete_mode_legacy_database(path)
    source_hash = _sha256(path)
    source_mtime = path.stat().st_mtime_ns

    database = Database(path)
    integrity_results = iter(["ok", "injected corruption"])
    monkeypatch.setattr(
        database_module,
        "sqlite_integrity_check",
        lambda _path: next(integrity_results),
    )
    try:
        with pytest.raises(RuntimeError, match="failed integrity check before migration"):
            database.initialize()
    finally:
        database.engine.dispose()

    assert _sha256(path) == source_hash
    assert path.stat().st_mtime_ns == source_mtime
    assert _journal_mode(path) == "delete"
    assert not _sidecars(path)
    assert not (tmp_path / "backups").exists()


def test_initialize_recovers_hot_delete_journal_after_process_crash(
    tmp_path: Path,
) -> None:
    path = tmp_path / "hot-journal.db"
    initialized = Database(path)
    initialized.initialize()
    initialized.engine.dispose()

    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE crash_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO crash_probe(id, value) VALUES (?, ?)",
            [(index, f"old-{index}-" + "x" * 3900) for index in range(1, 769)],
        )
        connection.commit()
    finally:
        connection.close()

    crash_code = r"""
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("PRAGMA journal_mode=DELETE")
connection.execute("PRAGMA synchronous=FULL")
connection.execute("PRAGMA cache_size=5")
connection.execute("BEGIN IMMEDIATE")
connection.executemany(
    "UPDATE crash_probe SET value = ? WHERE id = ?",
    [(f"new-{index}-" + "y" * 3900, index) for index in range(1, 769)],
)
os._exit(91)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", crash_code, str(path)],
        check=False,
        timeout=30,
    )
    assert crashed.returncode == 91

    journal = Path(f"{path}-journal")
    assert journal.is_file()
    assert journal.stat().st_size > 512
    assert journal.read_bytes()[:8] == database_module.ROLLBACK_JOURNAL_MAGIC
    raw_hashes = {path.name: _sha256(path), journal.name: _sha256(journal)}

    recovered = Database(path)
    try:
        recovered.initialize()
        assert recovered.startup_recovery is not None
        records = {
            Path(str(item["path"])).name: item
            for item in recovered.startup_recovery["files"]
        }
        assert set(raw_hashes).issubset(records)
        assert {name: records[name]["sha256"] for name in raw_hashes} == raw_hashes
        assert Path(str(recovered.startup_recovery["manifest"])).is_file()
        with recovered.engine.connect() as check:
            assert check.exec_driver_sql(
                "SELECT count(*) FROM crash_probe WHERE value LIKE 'old-%'"
            ).scalar_one() == 768
            assert check.exec_driver_sql(
                "SELECT count(*) FROM crash_probe WHERE value LIKE 'new-%'"
            ).scalar_one() == 0
            assert check.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
            assert check.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        recovered.engine.dispose()

    assert not journal.exists()


def test_initialize_rejects_forged_current_head_and_missing_fts_artifacts(
    tmp_path: Path,
) -> None:
    forged = tmp_path / "forged-current-head.db"
    connection = sqlite3.connect(forged)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_versions (
                version INTEGER PRIMARY KEY,
                applied_at DATETIME NOT NULL
            );
            INSERT INTO schema_versions(version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
            INSERT INTO alembic_version(version_num) VALUES ('0005');
            """
        )
        connection.commit()
    finally:
        connection.close()

    database = Database(forged)
    try:
        with pytest.raises(
            DatabaseSchemaError,
            match="missing ORM tables|search trigger set",
        ):
            database.initialize()
    finally:
        database.engine.dispose()

    missing_trigger = tmp_path / "missing-search-trigger.db"
    database = Database(missing_trigger)
    database.initialize()
    database.engine.dispose()
    connection = sqlite3.connect(missing_trigger)
    try:
        connection.execute("DROP TRIGGER rm_search_task_ai")
        connection.commit()
    finally:
        connection.close()

    reopened = Database(missing_trigger)
    try:
        with pytest.raises(DatabaseSchemaError, match="search trigger set"):
            reopened.initialize()
    finally:
        reopened.engine.dispose()
