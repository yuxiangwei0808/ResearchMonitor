from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command as alembic_command
from sqlalchemy import event, inspect, text

import research_monitor.database as database_module
from research_monitor.database import Database
from research_monitor.migrations.schema_v0001 import V0001_METADATA
from research_monitor.migrations.schema_v0002 import GRAPH_VIEWPORT_TABLE


class InjectedMigrationFailure(RuntimeError):
    pass


def test_failed_revision_rolls_back_ddl_stamp_and_injected_writes_then_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Database(tmp_path / "failure-injection.db")
    real_upgrade = database_module.command.upgrade

    def fail_after_real_revision(config, revision: str) -> None:
        real_upgrade(config, revision)
        if revision == "0002":
            connection = config.attributes["connection"]
            assert connection.in_transaction()
            connection.exec_driver_sql(
                "CREATE TABLE injected_uncommitted_write (id INTEGER PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "INSERT INTO injected_uncommitted_write(id) VALUES (1)"
            )
            raise InjectedMigrationFailure("fail after real Alembic DDL and stamp")

    monkeypatch.setattr(database_module.command, "upgrade", fail_after_real_revision)
    with pytest.raises(InjectedMigrationFailure):
        database.initialize()

    with database.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
    assert "graph_viewports" not in tables
    assert "injected_uncommitted_write" not in tables
    assert database.integrity_check() == "ok"

    # The failed attempt committed 0001 but nothing from 0002. Restoring the
    # real runner must resume at 0002 and reach the head exactly once.
    monkeypatch.setattr(database_module.command, "upgrade", real_upgrade)
    database.initialize()
    with database.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
    assert "graph_viewports" in tables
    assert "injected_uncommitted_write" not in tables

    backups_after_recovery = sorted(
        (tmp_path / "backups").glob("pre-migration-*.db")
    )
    database.initialize()
    assert sorted((tmp_path / "backups").glob("pre-migration-*.db")) == (
        backups_after_recovery
    )
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM graph_viewports")) == 0
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
    database.engine.dispose()


def test_historically_partial_revision_resumes_without_recreating_existing_table(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "partial-resume.db")
    alembic_command.upgrade(database._alembic_config(), "0001")
    project_id = str(uuid4())
    viewport_id = str(uuid4())
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id,name,root_path,description,research_goal,success_criteria,color,"
                "semantic_revision,layout_revision,entity_version,created_at,updated_at) "
                "VALUES (:id,'Partial migration project',:root,'','','','#4f46e5',"
                "0,0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"id": project_id, "root": str(tmp_path / "project")},
        )
    # Simulate the old unsafe runner committing revision 0002's DDL before its
    # Alembic version stamp. The idempotent revision should adopt this table and
    # preserve its row when startup resumes from stamp 0001.
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE graph_viewports (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                project_id VARCHAR(36) NOT NULL,
                scope_id VARCHAR(36) DEFAULT 'root' NOT NULL,
                x FLOAT DEFAULT '0' NOT NULL,
                y FLOAT DEFAULT '0' NOT NULL,
                zoom FLOAT DEFAULT '1' NOT NULL,
                entity_version INTEGER DEFAULT '1' NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                CONSTRAINT uq_graph_viewport_scope UNIQUE (project_id, scope_id)
            )
            """
        )
        connection.execute(
            text(
                "INSERT INTO graph_viewports "
                "(id, project_id, scope_id, x, y, zoom, entity_version) "
                "VALUES (:id, :project_id, 'root', 12, 34, 1.25, 2)"
            ),
            {"id": viewport_id, "project_id": project_id},
        )

    database.initialize()
    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        viewport = connection.execute(
            text(
                "SELECT id, project_id, scope_id, x, y, zoom, entity_version "
                "FROM graph_viewports WHERE id = :id"
            ),
            {"id": viewport_id},
        ).mappings().one()
        assert dict(viewport) == {
            "id": viewport_id,
            "project_id": project_id,
            "scope_id": "root",
            "x": 12.0,
            "y": 34.0,
            "zoom": 1.25,
            "entity_version": 2,
        }
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    backups_after_recovery = sorted(
        (tmp_path / "backups").glob("pre-migration-*.db")
    )
    assert len(backups_after_recovery) == 4
    database.initialize()
    assert sorted((tmp_path / "backups").glob("pre-migration-*.db")) == (
        backups_after_recovery
    )
    with database.engine.connect() as connection:
        assert connection.scalar(
            text("SELECT count(*) FROM graph_viewports WHERE id = :id"),
            {"id": viewport_id},
        ) == 1
    assert database.integrity_check() == "ok"
    database.engine.dispose()


def test_revision_0004_repairs_and_stamp_roll_back_together_then_retry(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "0004-rollback.db")
    V0001_METADATA.create_all(database.engine)
    GRAPH_VIEWPORT_TABLE.create(bind=database.engine)
    with database.engine.begin() as connection:
        connection.execute(text("DROP TABLE source_references"))
        connection.execute(text("DROP TABLE task_edges"))
        connection.execute(
            text(
                """
                CREATE TABLE source_references (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    task_id VARCHAR(36),
                    source_path TEXT NOT NULL,
                    anchor TEXT NOT NULL,
                    fingerprint VARCHAR(128) NOT NULL,
                    imported_at DATETIME NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE task_edges (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    source_id VARCHAR(36) NOT NULL,
                    target_id VARCHAR(36) NOT NULL,
                    edge_type VARCHAR(20) NOT NULL,
                    waived_reason TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    entity_version INTEGER NOT NULL,
                    deleted_at DATETIME,
                    created_at DATETIME NOT NULL,
                    UNIQUE (project_id, source_id, target_id, edge_type),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
                """
            )
        )
    alembic_command.upgrade(database._alembic_config(), "0003")

    with database.engine.connect() as connection:
        inspector = inspect(connection)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
        assert "opaque_key" not in {
            column["name"] for column in inspector.get_columns("source_references")
        }
        edge_columns = {
            column["name"] for column in inspector.get_columns("task_edges")
        }
        assert "disabled_batch_id" in edge_columns
        assert "disabled_reason" not in edge_columns

    statements: list[str] = []

    def fail_at_0004_stamp(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        statements.append(normalized)
        if not normalized.startswith("update alembic_version set version_num"):
            return
        assert any(
            "alter table source_references add column opaque_key" in item
            for item in statements
        )
        assert any(
            "create unique index ux_source_references_identity" in item
            for item in statements
        )
        assert any(
            "alter table task_edges add column disabled_reason" in item
            for item in statements
        )
        raise InjectedMigrationFailure("fail while stamping revision 0004")

    event.listen(database.engine, "before_cursor_execute", fail_at_0004_stamp)
    try:
        with pytest.raises(InjectedMigrationFailure):
            database.initialize()
    finally:
        event.remove(database.engine, "before_cursor_execute", fail_at_0004_stamp)

    source_identity = ("project_id", "source_path", "anchor", "opaque_key")
    source_identity_v2 = (
        "project_id",
        "source_root_id",
        "source_path",
        "anchor",
        "opaque_key",
    )
    with database.engine.connect() as connection:
        inspector = inspect(connection)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
        assert "opaque_key" not in {
            column["name"] for column in inspector.get_columns("source_references")
        }
        assert "disabled_reason" not in {
            column["name"] for column in inspector.get_columns("task_edges")
        }
        unique_shapes = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("source_references")
        } | {
            tuple(item["column_names"])
            for item in inspector.get_indexes("source_references")
            if item.get("unique")
        }
        assert source_identity not in unique_shapes
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    database.initialize()
    with database.engine.connect() as connection:
        inspector = inspect(connection)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert "opaque_key" in {
            column["name"] for column in inspector.get_columns("source_references")
        }
        assert "disabled_reason" in {
            column["name"] for column in inspector.get_columns("task_edges")
        }
        unique_shapes = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("source_references")
        } | {
            tuple(item["column_names"])
            for item in inspector.get_indexes("source_references")
            if item.get("unique")
        }
        assert source_identity_v2 in unique_shapes
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    assert database.integrity_check() == "ok"
    database.engine.dispose()
