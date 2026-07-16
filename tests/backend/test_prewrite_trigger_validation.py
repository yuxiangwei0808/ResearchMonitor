from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_monitor.database import Database, DatabaseSchemaError


PROJECT_ID = "00000000-0000-0000-0000-000000000001"


def _initialize(path: Path) -> None:
    database = Database(path)
    try:
        database.initialize()
    finally:
        database.engine.dispose()


def _insert_project(connection: sqlite3.Connection, root: Path) -> None:
    connection.execute(
        """
        INSERT INTO projects(
            id, name, root_path, description, research_goal,
            success_criteria, color, semantic_revision, layout_revision,
            entity_version, archived_at, trashed_at, last_manual_update_at,
            last_proposal_at, last_agent_sync_at, created_at, updated_at
        ) VALUES (?, ?, ?, '', '', '', '#0f766e', 0, 0, 1,
                  NULL, NULL, NULL, NULL, NULL, ?, ?)
        """,
        (
            PROJECT_ID,
            "Trigger guard",
            str(root),
            "2026-07-16T00:00:00Z",
            "2026-07-16T00:00:00Z",
        ),
    )


def _assert_initialize_rejected(path: Path, trigger_name: str) -> None:
    database = Database(path)
    try:
        with pytest.raises(DatabaseSchemaError, match=f"unexpected {trigger_name}"):
            database.initialize()
    finally:
        database.engine.dispose()


def test_rogue_schema_version_trigger_is_rejected_before_data_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rogue-schema-version.db"
    _initialize(path)
    trigger_name = "rogue_schema_version_insert"
    with sqlite3.connect(path) as connection:
        _insert_project(connection, tmp_path / "project")
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        connection.execute("DELETE FROM schema_versions")
        connection.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            AFTER INSERT ON schema_versions
            BEGIN
              DELETE FROM projects;
            END
            """
        )

    _assert_initialize_rejected(path, trigger_name)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM projects").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM schema_versions").fetchone() == (0,)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == revision
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name=?",
            (trigger_name,),
        ).fetchone() == (1,)


def test_current_head_rejects_arbitrarily_named_trigger(tmp_path: Path) -> None:
    path = tmp_path / "rogue-current-head.db"
    _initialize(path)
    trigger_name = "rogue_task_insert"
    with sqlite3.connect(path) as connection:
        _insert_project(connection, tmp_path / "project")
        connection.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            AFTER INSERT ON tasks
            BEGIN
              DELETE FROM projects;
            END
            """
        )

    _assert_initialize_rejected(path, trigger_name)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM projects").fetchone() == (1,)
        assert connection.execute("SELECT version FROM schema_versions").fetchone() == (1,)


def test_rogue_revision_0004_trigger_is_rejected_before_repair_dml(
    tmp_path: Path,
) -> None:
    path = tmp_path / "rogue-revision-0004.db"
    _initialize(path)
    trigger_name = "rogue_source_reference_update"
    with sqlite3.connect(path) as connection:
        _insert_project(connection, tmp_path / "project")
        connection.execute("UPDATE alembic_version SET version_num='0003'")
        connection.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            AFTER UPDATE ON source_references
            BEGIN
              DELETE FROM projects;
            END
            """
        )

    _assert_initialize_rejected(path, trigger_name)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM projects").fetchone() == (1,)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0003",)
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name=?",
            (trigger_name,),
        ).fetchone() == (1,)
