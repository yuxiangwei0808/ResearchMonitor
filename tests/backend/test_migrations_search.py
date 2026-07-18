from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from research_monitor.database import Database
from research_monitor.migrations.schema_v0001 import (
    V0001_METADATA,
    V0001_TABLE_NAMES,
    reflected_full_unique_shapes,
    validate_v0001_adopted_schema,
)
from research_monitor.models import Base, Pipeline, Project, SchemaVersion, Task
from research_monitor import sqlite_backup as sqlite_backup_module

from .conftest import enroll, mutate
from .test_api import op


SOURCE_IDENTITY = ("project_id", "source_path", "anchor", "opaque_key")
SOURCE_IDENTITY_V2 = (
    "project_id",
    "source_root_id",
    "source_path",
    "anchor",
    "opaque_key",
)


def _insert_v0001_project_with_tasks(
    connection,
    *,
    project_id: str,
    pipeline_id: str,
    root_path: Path,
    tasks: list[tuple[str, str, str]],
) -> None:
    now = datetime.now(timezone.utc)
    connection.execute(
        V0001_METADATA.tables["projects"].insert(),
        {
            "id": project_id,
            "name": "Legacy project",
            "root_path": str(root_path),
            "description": "",
            "research_goal": "",
            "success_criteria": "",
            "color": "#4f46e5",
            "semantic_revision": 0,
            "layout_revision": 0,
            "entity_version": 1,
            "created_at": now,
            "updated_at": now,
        },
    )
    connection.execute(
        V0001_METADATA.tables["pipelines"].insert(),
        {
            "id": pipeline_id,
            "project_id": project_id,
            "title": "Legacy pipeline",
            "description": "",
            "flow_mode": "sequential",
            "order_index": 0.0,
            "entity_version": 1,
            "created_at": now,
            "updated_at": now,
        },
    )
    connection.execute(
        V0001_METADATA.tables["tasks"].insert(),
        [
            {
                "id": task_id,
                "project_id": project_id,
                "pipeline_id": pipeline_id,
                "user_key": user_key,
                "kind": "task",
                "title": title,
                "description": "",
                "status": "planned",
                "outcome": "not_applicable",
                "priority": "recommended",
                "labels_json": "[]",
                "order_index": float(index),
                "completion_criteria": "",
                "blocker_reason": "",
                "completion_summary": "",
                "completion_actor": "",
                "completion_source": "",
                "completion_override_reason": "",
                "completion_provenance": "",
                "child_flow_mode": "freeform",
                "entity_version": 1,
                "created_at": now,
                "updated_at": now,
            }
            for index, (task_id, user_key, title) in enumerate(tasks)
        ],
    )


def _replace_source_references_without_full_identity(connection) -> None:
    connection.execute(text("DROP TABLE source_references"))
    connection.execute(
        text(
            """
            CREATE TABLE source_references (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                project_id VARCHAR(36) NOT NULL,
                task_id VARCHAR(36),
                source_path TEXT NOT NULL,
                anchor TEXT NOT NULL,
                opaque_key VARCHAR(240) NOT NULL,
                fingerprint VARCHAR(128) NOT NULL,
                imported_at DATETIME NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
            """
        )
    )


def test_fresh_database_is_at_alembic_head_with_fts(database: Database) -> None:
    with database.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        fts5 = connection.scalar(
            text(
                "SELECT 1 FROM pragma_module_list "
                "WHERE name = 'fts5'"
            )
        )
    assert revision == "0005"
    assert "research_search" in tables
    assert fts5 == 1
    assert not list((database.path.parent / "backups").glob("pre-migration-*.db"))


def test_revision_0001_rejects_ordinary_reserved_search_table_before_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ordinary-search-table.db"
    legacy = Database(path)
    Base.metadata.create_all(legacy.engine)
    with legacy.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE research_search "
                "(marker TEXT NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO research_search(marker) VALUES ('preserve me')")
        )
        connection.execute(
            text(
                "CREATE TRIGGER rm_search_task_ai AFTER INSERT ON tasks "
                "BEGIN SELECT 1; END"
            )
        )
    legacy.engine.dispose()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="expected an FTS5 virtual table"):
        rejected.initialize()
    rejected.engine.dispose()

    with sqlite3.connect(path) as connection:
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'research_search'"
        ).fetchone()
        marker = connection.execute(
            "SELECT marker FROM research_search"
        ).fetchone()
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = 'rm_search_task_ai'"
        ).fetchone()
        version_table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        revision = (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if version_table
            else None
        )
    assert ddl is not None and ddl[0].startswith("CREATE TABLE research_search")
    assert marker == ("preserve me",)
    assert trigger is not None and "SELECT 1" in trigger[0]
    assert revision is None


def test_revision_0001_rebuilds_owned_fts_and_replaces_reserved_triggers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "owned-fts-rebuild.db"
    legacy = Database(path)
    Base.metadata.create_all(legacy.engine)
    project_id, pipeline_id, task_id = (str(uuid4()) for _ in range(3))
    with legacy.Session.begin() as session:
        session.add(
            Project(
                id=project_id,
                name="Owned FTS",
                root_path=str(tmp_path / "owned-fts-project"),
            )
        )
        session.flush()
        session.add(
            Pipeline(
                id=pipeline_id,
                project_id=project_id,
                title="Search migration",
            )
        )
        session.flush()
        session.add(
            Task(
                id=task_id,
                project_id=project_id,
                pipeline_id=pipeline_id,
                title="Preexisting nebula evidence",
            )
        )
    with legacy.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE research_search USING fts5(
                    project_id UNINDEXED,
                    entity_type UNINDEXED,
                    entity_id UNINDEXED,
                    title,
                    content,
                    tokenize = 'unicode61 remove_diacritics 0'
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO research_search"
                "(project_id, entity_type, entity_id, title, content) "
                "VALUES ('', 'stale', 'stale-id', 'staleonlytoken', '')"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER rm_search_task_ai AFTER INSERT ON tasks "
                "BEGIN SELECT 1; END"
            )
        )
    legacy.engine.dispose()

    migrated = Database(path)
    migrated.initialize()
    fresh_task_id = str(uuid4())
    with migrated.Session.begin() as session:
        session.add(
            Task(
                id=fresh_task_id,
                project_id=project_id,
                pipeline_id=pipeline_id,
                title="Fresh aurora evidence",
            )
        )

    expected_triggers = {
        "rm_search_task_ai",
        "rm_search_task_au",
        "rm_search_task_ad",
        "rm_search_journal_ai",
        "rm_search_journal_au",
        "rm_search_journal_ad",
        "rm_search_artifact_ai",
        "rm_search_artifact_au",
        "rm_search_artifact_ad",
    }
    with migrated.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        ddl = connection.scalar(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'research_search'"
            )
        )
        triggers = {
            str(row["name"]): str(row["sql"])
            for row in connection.execute(
                text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'trigger' AND name LIKE 'rm_search_%'"
                )
            ).mappings()
        }
        assert set(triggers) == expected_triggers
        assert all("select 1" not in sql.casefold() for sql in triggers.values())
        assert "remove_diacritics 2" in str(ddl)
        assert connection.scalar(
            text(
                "SELECT count(*) FROM research_search "
                "WHERE research_search MATCH 'staleonlytoken'"
            )
        ) == 0
        assert connection.scalar(
            text(
                "SELECT count(*) FROM research_search "
                "WHERE research_search MATCH 'nebula' AND entity_id = :entity_id"
            ),
            {"entity_id": task_id},
        ) == 1
        assert connection.scalar(
            text(
                "SELECT count(*) FROM research_search "
                "WHERE research_search MATCH 'aurora' AND entity_id = :entity_id"
            ),
            {"entity_id": fresh_task_id},
        ) == 1
    migrated.engine.dispose()


def test_pre_migration_backup_is_private_atomic_and_does_not_write_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_records VALUES ('preserve me')")

    path.chmod(0o400)
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    source_mtime = path.stat().st_mtime_ns
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    backup_directory.chmod(0o777)
    database = Database(path)
    try:
        backup = database._verified_pre_migration_backup("test-hardening")
    finally:
        database.engine.dispose()

    assert backup.parent == backup_directory
    assert backup.stat().st_mode & 0o777 == 0o600
    assert backup_directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o400
    assert path.stat().st_mtime_ns == source_mtime
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source_hash
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()
    assert not list(backup_directory.glob(".*.tmp"))
    with sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT value FROM legacy_records").fetchone() == (
            "preserve me",
        )


def test_pre_migration_backup_integrity_failure_removes_private_temporary_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_records VALUES ('preserve me')")

    real_integrity_result = sqlite_backup_module._integrity_result
    integrity_checks = 0

    def fail_destination_integrity(connection: sqlite3.Connection) -> str:
        nonlocal integrity_checks
        integrity_checks += 1
        if integrity_checks == 2:
            return "injected failure"
        return real_integrity_result(connection)

    monkeypatch.setattr(
        sqlite_backup_module,
        "_integrity_result",
        fail_destination_integrity,
    )
    database = Database(path)
    try:
        with pytest.raises(RuntimeError, match="Pre-migration backup failed"):
            database._verified_pre_migration_backup("test-failure")
    finally:
        database.engine.dispose()

    backup_directory = tmp_path / "backups"
    assert integrity_checks == 2
    assert backup_directory.stat().st_mode & 0o777 == 0o700
    assert not list(backup_directory.iterdir())


def test_revision_0001_uses_frozen_metadata_independent_of_live_models() -> None:
    revision = import_module(
        "research_monitor.migrations.versions.0001_initial_schema_and_fts"
    )
    revision_source = Path(revision.__file__).read_text(encoding="utf-8")
    snapshot_source = Path(
        import_module("research_monitor.migrations.schema_v0001").__file__
    ).read_text(encoding="utf-8")

    assert "research_monitor.models" not in revision_source
    assert "research_monitor.models" not in snapshot_source
    assert "Base.metadata" not in revision_source
    assert "Base.metadata" not in snapshot_source
    assert V0001_METADATA is not Base.metadata
    assert "graph_viewports" not in V0001_TABLE_NAMES
    assert "deletion_batch_id" not in V0001_METADATA.tables["pipelines"].c
    assert "deletion_batch_id" not in V0001_METADATA.tables["tasks"].c
    assert "disabled_batch_id" not in V0001_METADATA.tables["task_edges"].c
    assert "validation_warning" not in V0001_METADATA.tables["artifacts"].c


def test_legacy_create_all_database_is_backed_up_adopted_and_backfilled(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    legacy = Database(path)
    Base.metadata.create_all(legacy.engine)
    project_id, pipeline_id, task_id = (str(uuid4()) for _ in range(3))
    with legacy.Session.begin() as session:
        session.add(SchemaVersion(version=1))
        session.add(
            Project(
                id=project_id,
                name="Legacy",
                root_path=str(tmp_path),
            )
        )
        session.flush()
        session.add(
            Pipeline(
                id=pipeline_id,
                project_id=project_id,
                title="Adopted pipeline",
            )
        )
        session.flush()
        session.add(
            Task(
                id=task_id,
                project_id=project_id,
                pipeline_id=pipeline_id,
                title="Backfilled quasar analysis",
            )
        )
    legacy.engine.dispose()

    migrated = Database(path)
    migrated.initialize()
    backups = list((tmp_path / "backups").glob("pre-migration-*-0005.db"))
    assert len(backups) == 1
    backup_connection = sqlite3.connect(backups[0])
    try:
        assert backup_connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        legacy_tables = {
            row[0]
            for row in backup_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "tasks" in legacy_tables
        assert "alembic_version" not in legacy_tables
    finally:
        backup_connection.close()

    with migrated.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        indexed = connection.scalar(
            text(
                "SELECT count(*) FROM research_search "
                "WHERE research_search MATCH 'quasar' AND entity_id = :task_id"
            ),
            {"task_id": task_id},
        )
    assert indexed == 1
    migrated.engine.dispose()


def test_frozen_revision_0001_upgrades_to_head_without_losing_data(tmp_path: Path) -> None:
    path = tmp_path / "existing-0001.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0001")
    project_id, pipeline_id, task_id = (str(uuid4()) for _ in range(3))
    now = datetime.now(timezone.utc)
    with staged.engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
        assert "graph_viewports" not in tables
        assert "deletion_batch_id" not in {
            item["name"] for item in inspect(connection).get_columns("tasks")
        }
        connection.execute(
            V0001_METADATA.tables["projects"].insert(),
            {
                "id": project_id,
                "name": "Historical project",
                "root_path": str(tmp_path / "historical-project"),
                "description": "Created by the frozen migration",
                "research_goal": "Preserve historical rows",
                "success_criteria": "Upgrade without data loss",
                "color": "#334155",
                "semantic_revision": 7,
                "layout_revision": 2,
                "entity_version": 3,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            V0001_METADATA.tables["pipelines"].insert(),
            {
                "id": pipeline_id,
                "project_id": project_id,
                "title": "Historical pipeline",
                "description": "Must survive 0002 and 0003",
                "flow_mode": "sequential",
                "order_index": 0.0,
                "entity_version": 4,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            V0001_METADATA.tables["tasks"].insert(),
            {
                "id": task_id,
                "project_id": project_id,
                "pipeline_id": pipeline_id,
                "user_key": "HIST-1",
                "kind": "task",
                "title": "Preserve quasar evidence",
                "description": "Historical revision 0001 task",
                "status": "in_progress",
                "outcome": "not_applicable",
                "priority": "required",
                "labels_json": '["migration"]',
                "order_index": 0.0,
                "completion_criteria": "Row remains readable",
                "blocker_reason": "",
                "completion_summary": "",
                "completion_actor": "",
                "completion_source": "",
                "completion_override_reason": "",
                "completion_provenance": "",
                "child_flow_mode": "freeform",
                "entity_version": 5,
                "created_at": now,
                "updated_at": now,
            },
        )
        assert connection.scalar(
            text(
                "SELECT count(*) FROM research_search "
                "WHERE research_search MATCH 'quasar' AND entity_id = :task_id"
            ),
            {"task_id": task_id},
        ) == 1
    staged.engine.dispose()

    upgraded = Database(path)
    upgraded.initialize()
    with upgraded.engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        row = connection.execute(
            text(
                "SELECT title, status, entity_version, deletion_batch_id "
                "FROM tasks WHERE id = :task_id"
            ),
            {"task_id": task_id},
        ).mappings().one()
        indexed = connection.scalar(
            text(
                "SELECT count(*) FROM research_search "
                "WHERE research_search MATCH 'quasar' AND entity_id = :task_id"
            ),
            {"task_id": task_id},
        )

    assert revision == "0005"
    assert "graph_viewports" in tables
    assert dict(row) == {
        "title": "Preserve quasar evidence",
        "status": "in_progress",
        "entity_version": 5,
        "deletion_batch_id": None,
    }
    assert indexed == 1
    with upgraded.engine.connect() as connection:
        inspector = inspect(connection)
        assert "deletion_batch_id" in {item["name"] for item in inspector.get_columns("pipelines")}
        assert "deletion_batch_id" in {item["name"] for item in inspector.get_columns("tasks")}
        assert "disabled_batch_id" in {item["name"] for item in inspector.get_columns("task_edges")}
        assert "validation_warning" in {item["name"] for item in inspector.get_columns("artifacts")}
    for revision_id in ("0002", "0003", "0004", "0005"):
        backups = list((tmp_path / "backups").glob(f"pre-migration-*-{revision_id}.db"))
        assert len(backups) == 1
        assert upgraded.integrity_check(backups[0]) == "ok"
    upgraded.engine.dispose()


def test_pre_v1_identity_columns_are_repaired_once_without_losing_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pre-v1-identities.db"
    legacy = Database(path)
    V0001_METADATA.create_all(legacy.engine)
    project_id, pipeline_id, source_task_id, target_task_id, edge_id = (
        str(uuid4()) for _ in range(5)
    )
    reference_a_id = "00000000-0000-0000-0000-000000000001"
    reference_b_id = "00000000-0000-0000-0000-000000000002"
    now = datetime.now(timezone.utc)
    with legacy.engine.begin() as connection:
        connection.execute(
            V0001_METADATA.tables["schema_versions"].insert(),
            {"version": 1, "applied_at": now},
        )
        _insert_v0001_project_with_tasks(
            connection,
            project_id=project_id,
            pipeline_id=pipeline_id,
            root_path=tmp_path,
            tasks=[
                (source_task_id, "LEGACY-1", "Legacy source task"),
                (target_task_id, "LEGACY-2", "Legacy target task"),
            ],
        )

    # Reproduce the released pre-v1 create-all shapes exactly: the source table
    # had no opaque key or identity constraint, and task edges lacked their
    # disabled-reason provenance column.
    with legacy.engine.begin() as connection:
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
        for reference_id, task_id, fingerprint in (
            (reference_a_id, source_task_id, "sha256:first"),
            (reference_b_id, target_task_id, "sha256:second"),
        ):
            connection.execute(
                text(
                    "INSERT INTO source_references "
                    "(id, project_id, task_id, source_path, anchor, fingerprint, imported_at) "
                    "VALUES (:id, :project_id, :task_id, 'PLAN.md', 'shared-anchor', "
                    ":fingerprint, :now)"
                ),
                {
                    "id": reference_id,
                    "project_id": project_id,
                    "task_id": task_id,
                    "fingerprint": fingerprint,
                    "now": now,
                },
            )
        connection.execute(
            text(
                "INSERT INTO task_edges "
                "(id, project_id, source_id, target_id, edge_type, waived_reason, "
                "enabled, entity_version, deleted_at, created_at) "
                "VALUES (:id, :project_id, :source_id, :target_id, 'dependency', "
                "'legacy waiver', 0, 3, NULL, :now)"
            ),
            {
                "id": edge_id,
                "project_id": project_id,
                "source_id": source_task_id,
                "target_id": target_task_id,
                "now": now,
            },
        )
    legacy.engine.dispose()

    migrated = Database(path)
    migrated.initialize()

    backup = next((tmp_path / "backups").glob("pre-migration-*-0001.db"))
    with sqlite3.connect(backup) as backup_connection:
        source_columns = {
            row[1] for row in backup_connection.execute("PRAGMA table_info(source_references)")
        }
        edge_columns = {
            row[1] for row in backup_connection.execute("PRAGMA table_info(task_edges)")
        }
        assert "opaque_key" not in source_columns
        assert "disabled_reason" not in edge_columns
        assert backup_connection.execute("SELECT count(*) FROM source_references").fetchone() == (2,)
        assert backup_connection.execute("SELECT count(*) FROM task_edges").fetchone() == (1,)

    with migrated.engine.connect() as connection:
        inspector = inspect(connection)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert "opaque_key" in {
            column["name"] for column in inspector.get_columns("source_references")
        }
        assert "disabled_reason" in {
            column["name"] for column in inspector.get_columns("task_edges")
        }
        project_root_id = connection.scalar(
            text(
                "SELECT id FROM artifact_roots "
                "WHERE project_id=:project_id AND is_project_root=1"
            ),
            {"project_id": project_id},
        )
        assert project_root_id is not None
        references = connection.execute(
            text(
                "SELECT id, task_id, source_path, anchor, opaque_key, fingerprint "
                "FROM source_references ORDER BY id"
            )
        ).mappings().all()
        assert [dict(row) for row in references] == [
            {
                "id": reference_a_id,
                "task_id": source_task_id,
                "source_path": "PLAN.md",
                "anchor": "shared-anchor",
                "opaque_key": "",
                "fingerprint": "sha256:first",
            },
            {
                "id": reference_b_id,
                "task_id": target_task_id,
                "source_path": "PLAN.md",
                "anchor": "shared-anchor",
                "opaque_key": "LEGACY-2",
                "fingerprint": "sha256:second",
            },
        ]
        edge = connection.execute(
            text(
                "SELECT id, source_id, target_id, waived_reason, enabled, "
                "disabled_reason, disabled_batch_id, entity_version "
                "FROM task_edges WHERE id = :id"
            ),
            {"id": edge_id},
        ).mappings().one()
        assert dict(edge) == {
            "id": edge_id,
            "source_id": source_task_id,
            "target_id": target_task_id,
            "waived_reason": "legacy waiver",
            "enabled": 0,
            "disabled_reason": "",
            "disabled_batch_id": None,
            "entity_version": 3,
        }
        unique_shapes = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints("source_references")
        } | {
            tuple(item["column_names"])
            for item in inspector.get_indexes("source_references")
            if item.get("unique")
        }
        assert SOURCE_IDENTITY_V2 in unique_shapes
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    backups_before_retry = sorted((tmp_path / "backups").glob("pre-migration-*.db"))
    migrated.initialize()
    backups_after_retry = sorted((tmp_path / "backups").glob("pre-migration-*.db"))
    assert backups_after_retry == backups_before_retry
    with migrated.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO source_references "
                "(id, project_id, task_id, source_root_id, source_path, anchor, "
                "opaque_key, fingerprint, imported_at) "
                "VALUES (:id, :project_id, :task_id, :source_root_id, "
                "'PLAN.md', 'shared-anchor', "
                "'THIRD', 'sha256:third', :now)"
            ),
            {
                "id": str(uuid4()),
                "project_id": project_id,
                "task_id": target_task_id,
                "source_root_id": project_root_id,
                "now": now,
            },
        )
    with pytest.raises(IntegrityError):
        with migrated.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_references "
                    "(id, project_id, task_id, source_root_id, source_path, anchor, "
                    "opaque_key, fingerprint, imported_at) "
                    "VALUES (:id, :project_id, :task_id, :source_root_id, "
                    "'PLAN.md', 'shared-anchor', "
                    "'', 'sha256:duplicate', :now)"
                ),
                {
                    "id": str(uuid4()),
                    "project_id": project_id,
                    "task_id": source_task_id,
                    "source_root_id": project_root_id,
                    "now": now,
                },
            )
    with migrated.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM source_references")) == 3
    assert migrated.integrity_check() == "ok"
    migrated.engine.dispose()


def test_legacy_lookalike_without_foreign_keys_is_rejected_before_repair(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lookalike.db"
    legacy = Database(path)
    V0001_METADATA.create_all(legacy.engine)
    with legacy.engine.begin() as connection:
        connection.execute(text("DROP TABLE source_references"))
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
                    imported_at DATETIME NOT NULL
                )
                """
            )
        )
    legacy.engine.dispose()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="foreign key shape"):
        rejected.initialize()
    rejected.engine.dispose()

    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(source_references)")
        }
        version_table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        revision = (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if version_table
            else None
        )
    assert "opaque_key" not in columns
    assert revision is None
    backups = list((tmp_path / "backups").glob("pre-migration-*-0001.db"))
    assert len(backups) == 1


def test_revision_0004_accepts_safe_partially_persisted_repair(
    tmp_path: Path,
) -> None:
    path = tmp_path / "partial-0004.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0003")
    project_id, pipeline_id, task_id = (str(uuid4()) for _ in range(3))
    with staged.engine.begin() as connection:
        _insert_v0001_project_with_tasks(
            connection,
            project_id=project_id,
            pipeline_id=pipeline_id,
            root_path=tmp_path,
            tasks=[(task_id, "", "Recoverable nebula index")],
        )
    with staged.engine.begin() as connection:
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
                    opaque_key VARCHAR(240) NOT NULL DEFAULT '',
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
                """
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX ux_source_references_identity "
                "ON source_references "
                "(project_id, source_path, anchor, opaque_key)"
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
                    disabled_batch_id VARCHAR(36),
                    UNIQUE (project_id, source_id, target_id, edge_type),
                    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY(source_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES tasks(id) ON DELETE CASCADE
                )
                """
            )
        )
    staged.engine.dispose()

    migrated = Database(path)
    migrated.initialize()
    with migrated.engine.connect() as connection:
        inspector = inspect(connection)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert "opaque_key" in {
            column["name"] for column in inspector.get_columns("source_references")
        }
        assert "disabled_reason" in {
            column["name"] for column in inspector.get_columns("task_edges")
        }
        assert len(inspector.get_foreign_keys("source_references")) == 2
        assert len(inspector.get_foreign_keys("task_edges")) == 3
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        assert connection.scalar(
            text(
                "SELECT count(*) FROM research_search "
                "WHERE research_search MATCH 'nebula' AND entity_id = :task_id"
            ),
            {"task_id": task_id},
        ) == 1
    assert migrated.integrity_check() == "ok"
    migrated.engine.dispose()


@pytest.mark.parametrize(
    ("table_name", "fault", "message"),
    [
        ("pipelines", "primary_key", "primary key shape"),
        ("tasks", "foreign_keys", "foreign key shape"),
        ("projects", "unique", "unique constraint/index shape"),
        ("tasks", "required_index", "missing required indexes"),
        ("artifacts", "nullability", "unexpected nullability"),
        ("artifacts", "type", "unexpected type"),
    ],
)
def test_frozen_v0001_validator_fails_closed_on_structural_drift(
    tmp_path: Path,
    table_name: str,
    fault: str,
    message: str,
) -> None:
    database = Database(tmp_path / f"v0001-{table_name}-{fault}.db")
    command.upgrade(database._alembic_config(), "0004")

    class FaultyInspector:
        def __init__(self, base):
            self.base = base

        @property
        def bind(self):
            return self.base.bind

        def get_table_names(self):
            return self.base.get_table_names()

        def __getattr__(self, method_name):
            original = getattr(self.base, method_name)

            def overridden(candidate, *args, **kwargs):
                value = original(candidate, *args, **kwargs)
                if candidate != table_name:
                    return value
                if fault == "primary_key" and method_name == "get_pk_constraint":
                    return {"name": None, "constrained_columns": []}
                if fault == "foreign_keys" and method_name == "get_foreign_keys":
                    return []
                if fault == "unique" and method_name == "get_unique_constraints":
                    return []
                if fault == "required_index" and method_name == "get_indexes":
                    return []
                if fault in {"nullability", "type"} and method_name == "get_columns":
                    changed = [dict(column) for column in value]
                    target = next(column for column in changed if column["name"] == "notes")
                    if fault == "nullability":
                        target["nullable"] = not bool(target["nullable"])
                    else:
                        target["type"] = sa.Integer()
                    return changed
                return value

            return overridden

    with database.engine.connect() as connection:
        valid = inspect(connection)
        validate_v0001_adopted_schema(valid)
        with pytest.raises(RuntimeError, match=message):
            validate_v0001_adopted_schema(FaultyInspector(valid))
    database.engine.dispose()


def test_revision_0002_rejects_malformed_preexisting_graph_viewport(
    tmp_path: Path,
) -> None:
    path = tmp_path / "malformed-graph.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0001")
    with staged.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE graph_viewports (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    scope_id VARCHAR(36) NOT NULL DEFAULT 'root',
                    x FLOAT NOT NULL DEFAULT 0,
                    y FLOAT NOT NULL DEFAULT 0,
                    zoom FLOAT NOT NULL DEFAULT 1,
                    entity_version INTEGER NOT NULL DEFAULT 1
                )
                """
            )
        )
    staged.engine.dispose()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="graph_viewports; foreign key shape"):
        rejected.initialize()
    rejected.engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0001",
        )


def test_revision_0003_rejects_falsely_stamped_missing_graph_viewport(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-graph-at-0002.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0001")
    command.stamp(staged._alembic_config(), "0002")
    staged.engine.dispose()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="missing Research Monitor table: graph_viewports"):
        rejected.initialize()
    rejected.engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0002",
        )


def test_revision_0004_rejects_falsely_stamped_malformed_graph_viewport(
    tmp_path: Path,
) -> None:
    path = tmp_path / "malformed-graph-at-0003.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0003")
    staged.engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE graph_viewports")
        connection.execute(
            """
            CREATE TABLE graph_viewports (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                project_id VARCHAR(36) NOT NULL,
                scope_id VARCHAR(36) NOT NULL DEFAULT 'root',
                x FLOAT NOT NULL DEFAULT 0,
                y FLOAT NOT NULL DEFAULT 0,
                zoom FLOAT NOT NULL DEFAULT 1,
                entity_version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        connection.commit()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="graph_viewports; foreign key shape"):
        rejected.initialize()
    rejected.engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0003",
        )


def test_revision_0003_rejects_malformed_preexisting_additive_column(
    tmp_path: Path,
) -> None:
    path = tmp_path / "malformed-0003.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0002")
    with staged.engine.begin() as connection:
        connection.execute(text("ALTER TABLE pipelines ADD COLUMN deletion_batch_id INTEGER"))
    staged.engine.dispose()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="deletion_batch_id has unexpected type"):
        rejected.initialize()
    rejected.engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0002",
        )


def test_unstamped_partial_0004_identity_is_completed_at_head(tmp_path: Path) -> None:
    path = tmp_path / "unstamped-partial-0004.db"
    staged = Database(path)
    V0001_METADATA.create_all(staged.engine)
    with staged.engine.begin() as connection:
        _replace_source_references_without_full_identity(connection)
    staged.engine.dispose()

    migrated = Database(path)
    migrated.initialize()
    with migrated.engine.connect() as connection:
        inspector = inspect(connection)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert SOURCE_IDENTITY_V2 in reflected_full_unique_shapes(
            inspector,
            "source_references",
        )
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
    migrated.engine.dispose()


@pytest.mark.parametrize(
    "index_ddl",
    [
        (
            "CREATE UNIQUE INDEX ux_partial_source_identity ON source_references "
            "(project_id, source_path, anchor, opaque_key) "
            "WHERE length(opaque_key) > 0"
        ),
        (
            "CREATE UNIQUE INDEX ux_expression_source_identity ON source_references "
            "(project_id, lower(source_path), anchor, opaque_key)"
        ),
    ],
    ids=["partial", "expression"],
)
def test_unstamped_non_full_source_identity_is_rejected(
    tmp_path: Path,
    index_ddl: str,
) -> None:
    path = tmp_path / "non-full-source-identity.db"
    staged = Database(path)
    V0001_METADATA.create_all(staged.engine)
    with staged.engine.begin() as connection:
        _replace_source_references_without_full_identity(connection)
        connection.execute(text(index_ddl))
    staged.engine.dispose()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="unique constraint/index shape"):
        rejected.initialize()
    rejected.engine.dispose()

    with sqlite3.connect(path) as connection:
        version_table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()
        revision = (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if version_table
            else None
        )
    assert revision is None


def test_revision_0003_rejects_a_missing_managed_table(tmp_path: Path) -> None:
    path = tmp_path / "missing-0003-table.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0002")
    staged.engine.dispose()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE artifacts")
        connection.commit()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="missing tables: artifacts"):
        rejected.initialize()
    rejected.engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0002",
        )


def test_revision_0004_requires_all_revision_0003_columns(tmp_path: Path) -> None:
    path = tmp_path / "falsely-stamped-0003.db"
    staged = Database(path)
    command.upgrade(staged._alembic_config(), "0002")
    command.stamp(staged._alembic_config(), "0003")
    staged.engine.dispose()

    rejected = Database(path)
    with pytest.raises(RuntimeError, match="artifacts; missing columns: validation_warning"):
        rejected.initialize()
    rejected.engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0003",
        )


def test_search_endpoint_indexes_updates_filters_and_soft_deletes(
    client,
    project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id, journal_id, artifact_id = (str(uuid4()) for _ in range(4))
    changed = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Analysis"}),
            op(
                "task.create",
                {
                    "id": task_id,
                    "pipeline_id": pipeline_id,
                    "user_key": "CAL-7",
                    "title": "Analyze calibration residuals",
                    "description": "Compare robust estimators",
                    "priority": "required",
                    "labels": ["statistics", "paper"],
                },
            ),
            op(
                "journal.create",
                {
                    "id": journal_id,
                    "task_id": task_id,
                    "entry_type": "decision",
                    "content": "Chose the Huber calibration estimator",
                },
            ),
            op(
                "artifact.create",
                {
                    "id": artifact_id,
                    "kind": "url",
                    "locator": "https://wandb.ai/example/calibration",
                    "provider": "W&B",
                    "label": "Calibration dashboard",
                    "notes": "External run summary only",
                },
            ),
        ],
    )

    response = client.get(
        f"/api/v1/projects/{project['id']}/search",
        params={"q": "calibr"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {item["entity_type"] for item in body["results"]} == {
        "task",
        "journal",
        "artifact",
    }

    filtered = client.get(
        f"/api/v1/projects/{project['id']}/search",
        params={
            "q": "calibration",
            "entity_type": "task",
            "priority": "required",
            "readiness": "ready",
            "label": "statistics",
        },
    ).json()
    assert filtered["total"] == 1
    assert filtered["results"][0]["key"] == "CAL-7"
    assert filtered["results"][0]["readiness"] == "ready"

    artifact_only = client.get(
        f"/api/v1/projects/{project['id']}/search",
        params={
            "q": "wandb",
            "entity_type": "artifact",
            "artifact_type": "url",
        },
    ).json()
    assert [item["entity_id"] for item in artifact_only["results"]] == [artifact_id]

    updated = mutate(
        client,
        project,
        changed["semantic_revision"],
        [
            op(
                "task.update",
                {"title": "Analyze stellar residuals"},
                task_id,
                1,
            ),
            op("journal.delete", {}, journal_id, 1),
        ],
    )
    assert updated["semantic_revision"] == changed["semantic_revision"] + 1
    old_task = client.get(
        f"/api/v1/projects/{project['id']}/search",
        params={"q": "calibration", "entity_type": "task"},
    ).json()
    assert old_task["total"] == 0
    deleted_journal = client.get(
        f"/api/v1/projects/{project['id']}/search",
        params={"q": "Huber", "entity_type": "journal"},
    ).json()
    assert deleted_journal["total"] == 0
    new_task = client.get(
        f"/api/v1/projects/{project['id']}/search",
        params={"q": "stellar", "entity_type": "task"},
    ).json()
    assert [item["entity_id"] for item in new_task["results"]] == [task_id]

    invalid = client.get(
        f"/api/v1/projects/{project['id']}/search",
        params={"q": '"'},
    )
    assert invalid.status_code == 422
