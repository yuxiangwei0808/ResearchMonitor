"""Adopt the v1 relational schema and add the project search index.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from datetime import datetime, timezone
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from research_monitor.migrations.schema_v0001 import (
    V0001_METADATA,
    V0001_TABLE_NAMES,
    validate_v0001_adopted_schema,
)


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


SEARCH_TABLE = "research_search"
SEARCH_COLUMNS = ("project_id", "entity_type", "entity_id", "title", "content")
SEARCH_TRIGGER_NAMES = (
    "rm_search_task_ai",
    "rm_search_task_au",
    "rm_search_task_ad",
    "rm_search_journal_ai",
    "rm_search_journal_au",
    "rm_search_journal_ad",
    "rm_search_artifact_ai",
    "rm_search_artifact_au",
    "rm_search_artifact_ad",
)
SEARCH_CREATE_SQL = f"""
CREATE VIRTUAL TABLE {SEARCH_TABLE} USING fts5(
    project_id UNINDEXED,
    entity_type UNINDEXED,
    entity_id UNINDEXED,
    title,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""
_FTS5_TABLE_DDL = re.compile(
    r'^\s*CREATE\s+VIRTUAL\s+TABLE\s+'
    r'(?:"research_search"|\[research_search\]|\x60research_search\x60|research_search)'
    r'\s+USING\s+fts5\s*\(',
    re.IGNORECASE,
)


def _canonical_sql(value: str) -> str:
    return " ".join(value.strip().rstrip(";").split()).casefold()


def _validated_search_table(bind: sa.Connection) -> bool:
    row = bind.execute(
        sa.text(
            "SELECT type, sql FROM sqlite_master "
            "WHERE name = :name ORDER BY type LIMIT 1"
        ),
        {"name": SEARCH_TABLE},
    ).mappings().first()
    if row is None:
        return False
    ddl = str(row["sql"] or "")
    if row["type"] != "table" or _FTS5_TABLE_DDL.match(ddl) is None:
        raise RuntimeError(
            "Cannot adopt reserved table research_search; expected an FTS5 "
            "virtual table owned by Research Monitor"
        )
    visible_columns = tuple(
        str(item["name"])
        for item in bind.execute(
            sa.text(
                "SELECT name FROM pragma_table_xinfo(:table_name) "
                "WHERE hidden = 0 ORDER BY cid"
            ),
            {"table_name": SEARCH_TABLE},
        ).mappings()
    )
    if visible_columns != SEARCH_COLUMNS:
        raise RuntimeError(
            "Cannot adopt reserved FTS5 table research_search; visible column "
            "shape does not match Research Monitor"
        )
    return True


def _validate_search_objects(
    bind: sa.Connection,
    trigger_statements: list[str],
) -> None:
    if not _validated_search_table(bind):
        raise RuntimeError("Research Monitor search index was not created")
    expected_triggers = {
        name: _canonical_sql(statement)
        for name, statement in zip(
            SEARCH_TRIGGER_NAMES,
            trigger_statements,
            strict=True,
        )
    }
    actual_triggers = {
        str(row["name"]): _canonical_sql(str(row["sql"] or ""))
        for row in bind.execute(
            sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
        ).mappings()
        if row["name"] in expected_triggers
    }
    if actual_triggers != expected_triggers:
        raise RuntimeError(
            "Research Monitor search triggers do not match revision 0001"
        )

    validation_id = "__research_monitor_fts_validation__"
    bind.execute(
        sa.text(
            f"INSERT INTO {SEARCH_TABLE}"
            "(project_id, entity_type, entity_id, title, content) "
            "VALUES ('', 'validation', :entity_id, 'rmuniquevalidationtoken', '')"
        ),
        {"entity_id": validation_id},
    )
    try:
        matches = bind.scalar(
            sa.text(
                f"SELECT count(*) FROM {SEARCH_TABLE} "
                f"WHERE {SEARCH_TABLE} MATCH 'rmuniquevalidationtoken' "
                "AND entity_id = :entity_id"
            ),
            {"entity_id": validation_id},
        )
    finally:
        bind.execute(
            sa.text(
                f"DELETE FROM {SEARCH_TABLE} "
                "WHERE entity_type = 'validation' AND entity_id = :entity_id"
            ),
            {"entity_id": validation_id},
        )
    if matches != 1:
        raise RuntimeError("Research Monitor FTS5 search validation failed")


def _adopt_or_create_relational_schema() -> None:
    """Create a fresh schema or verify an existing create_all v1 schema.

    Pre-Alembic Research Monitor releases used the application's declarative
    metadata to create the v0001 tables. This migration verifies those tables
    against an immutable historical snapshot rather than today's ORM models.
    Complete legacy schemas are adopted without rebuilding tables or touching
    their rows. Column verification prevents Alembic from stamping or filling
    in a partially compatible database. One released pre-v1 shape omitted
    ``source_references.opaque_key`` and ``task_edges.disabled_reason``;
    revision 0004 performs those narrowly scoped, data-preserving repairs after
    this adoption revision is stamped.
    """

    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    expected_tables = set(V0001_TABLE_NAMES)
    managed_existing = expected_tables & existing_tables
    if not managed_existing:
        unrelated_tables = existing_tables - {"alembic_version"}
        if unrelated_tables:
            raise RuntimeError(
                "Refusing to initialize a non-Research Monitor SQLite database; "
                "unexpected tables: " + ", ".join(sorted(unrelated_tables))
            )
        V0001_METADATA.create_all(bind=bind)
        inspector = inspect(bind)
        existing_tables = set(inspector.get_table_names())
    missing_tables = sorted(expected_tables - existing_tables)
    if missing_tables:
        raise RuntimeError(
            "Cannot adopt incomplete Research Monitor schema; missing tables: "
            + ", ".join(missing_tables)
        )
    # A pre-transactional 0004 attempt may have added opaque_key before its
    # full identity index or Alembic stamp. A genuinely absent index is a
    # repairable partial state; malformed partial/expression indexes still fail.
    validate_v0001_adopted_schema(
        inspector,
        allow_partial_source_identity=True,
    )


def rebuild_search_index() -> None:
    """Rebuild the revision-owned, fully derived FTS objects.

    Revision 0004 also calls this frozen helper after repairing a historically
    partial relational upgrade. Keeping the canonical DDL and backfill in one
    revision-owned implementation prevents the two migration paths from
    drifting while preserving the reserved-table validation below.
    """

    bind = op.get_bind()
    existing_search_table = _validated_search_table(bind)
    # The reserved table must be verified before any owned trigger is touched.
    # An ordinary user table with this name is never treated as derived data.
    for trigger_name in SEARCH_TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    if existing_search_table:
        op.execute(f"DROP TABLE {SEARCH_TABLE}")
    op.execute(SEARCH_CREATE_SQL)

    statements = [
        f"""
        CREATE TRIGGER rm_search_task_ai
        AFTER INSERT ON tasks WHEN new.deleted_at IS NULL
        BEGIN
          INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
          VALUES (
            new.project_id, 'task', new.id, new.title,
            trim(
              coalesce(new.user_key, '') || ' ' ||
              coalesce(new.description, '') || ' ' ||
              coalesce(new.completion_criteria, '') || ' ' ||
              coalesce(new.blocker_reason, '') || ' ' ||
              coalesce(new.completion_summary, '') || ' ' ||
              coalesce(new.labels_json, '')
            )
          );
        END
        """,
        f"""
        CREATE TRIGGER rm_search_task_au
        AFTER UPDATE ON tasks
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'task' AND entity_id = old.id;
          INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
          SELECT
            new.project_id, 'task', new.id, new.title,
            trim(
              coalesce(new.user_key, '') || ' ' ||
              coalesce(new.description, '') || ' ' ||
              coalesce(new.completion_criteria, '') || ' ' ||
              coalesce(new.blocker_reason, '') || ' ' ||
              coalesce(new.completion_summary, '') || ' ' ||
              coalesce(new.labels_json, '')
            )
          WHERE new.deleted_at IS NULL;
        END
        """,
        f"""
        CREATE TRIGGER rm_search_task_ad
        AFTER DELETE ON tasks
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'task' AND entity_id = old.id;
        END
        """,
        f"""
        CREATE TRIGGER rm_search_journal_ai
        AFTER INSERT ON journal_entries WHEN new.deleted_at IS NULL
        BEGIN
          INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
          VALUES (new.project_id, 'journal', new.id, new.entry_type, new.content);
        END
        """,
        f"""
        CREATE TRIGGER rm_search_journal_au
        AFTER UPDATE ON journal_entries
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'journal' AND entity_id = old.id;
          INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
          SELECT new.project_id, 'journal', new.id, new.entry_type, new.content
          WHERE new.deleted_at IS NULL;
        END
        """,
        f"""
        CREATE TRIGGER rm_search_journal_ad
        AFTER DELETE ON journal_entries
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'journal' AND entity_id = old.id;
        END
        """,
        f"""
        CREATE TRIGGER rm_search_artifact_ai
        AFTER INSERT ON artifacts WHEN new.deleted_at IS NULL
        BEGIN
          INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
          VALUES (
            new.project_id, 'artifact', new.id, new.label,
            trim(
              coalesce(new.provider, '') || ' ' ||
              coalesce(new.locator, '') || ' ' ||
              coalesce(new.notes, '')
            )
          );
        END
        """,
        f"""
        CREATE TRIGGER rm_search_artifact_au
        AFTER UPDATE ON artifacts
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'artifact' AND entity_id = old.id;
          INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
          SELECT
            new.project_id, 'artifact', new.id, new.label,
            trim(
              coalesce(new.provider, '') || ' ' ||
              coalesce(new.locator, '') || ' ' ||
              coalesce(new.notes, '')
            )
          WHERE new.deleted_at IS NULL;
        END
        """,
        f"""
        CREATE TRIGGER rm_search_artifact_ad
        AFTER DELETE ON artifacts
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'artifact' AND entity_id = old.id;
        END
        """,
    ]
    for statement in statements:
        op.execute(statement)

    # Rebuilding makes adoption deterministic and repairs interrupted
    # pre-release indexes without changing a source entity.
    op.execute(f"DELETE FROM {SEARCH_TABLE}")
    op.execute(
        f"""
        INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
        SELECT
          project_id, 'task', id, title,
          trim(
            coalesce(user_key, '') || ' ' ||
            coalesce(description, '') || ' ' ||
            coalesce(completion_criteria, '') || ' ' ||
            coalesce(blocker_reason, '') || ' ' ||
            coalesce(completion_summary, '') || ' ' ||
            coalesce(labels_json, '')
          )
        FROM tasks
        WHERE deleted_at IS NULL
        """
    )
    op.execute(
        f"""
        INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
        SELECT project_id, 'journal', id, entry_type, content
        FROM journal_entries
        WHERE deleted_at IS NULL
        """
    )
    op.execute(
        f"""
        INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
        SELECT
          project_id, 'artifact', id, label,
          trim(
            coalesce(provider, '') || ' ' ||
            coalesce(locator, '') || ' ' ||
            coalesce(notes, '')
          )
        FROM artifacts
        WHERE deleted_at IS NULL
        """
    )
    _validate_search_objects(bind, statements)


def upgrade() -> None:
    _adopt_or_create_relational_schema()
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT OR IGNORE INTO schema_versions(version, applied_at) "
            "VALUES (:version, :applied_at)"
        ),
        {"version": 1, "applied_at": datetime.now(timezone.utc)},
    )
    rebuild_search_index()


def downgrade() -> None:
    # Relational tables may predate Alembic. Remove only revision-owned objects.
    for trigger in SEARCH_TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute(f"DROP TABLE IF EXISTS {SEARCH_TABLE}")
