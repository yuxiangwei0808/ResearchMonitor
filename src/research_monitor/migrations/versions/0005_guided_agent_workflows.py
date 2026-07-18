"""Add guided-agent workflow storage and frozen v0005 validation.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from research_monitor.migrations.schema_v0005 import (
    AGENT_INTENTS_TABLE,
    NEW_JOURNAL_UNIQUE,
    NEW_PROPOSAL_UNIQUE,
    NEW_SOURCE_UNIQUE,
    OLD_SOURCE_UNIQUE,
    PLANNING_PROFILES_TABLE,
    TASK_SOURCE_REFERENCES_TABLE,
    validate_v0005_partial_schema,
    validate_v0005_schema,
)


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


ADDITIVE_COLUMNS: dict[str, tuple[sa.Column[object], ...]] = {
    "projects": (
        sa.Column("last_agent_check_at", sa.DateTime(), nullable=True),
    ),
    "scan_policies": (
        sa.Column(
            "readable_source_root_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "max_files_per_scan",
            sa.Integer(),
            nullable=False,
            server_default="500",
        ),
        sa.Column(
            "max_total_text_bytes",
            sa.Integer(),
            nullable=False,
            server_default=str(10 * 1024 * 1024),
        ),
    ),
    "journal_entries": (
        sa.Column("origin_key", sa.String(length=240), nullable=True),
        sa.Column(
            "content_sha256",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    ),
    "proposals": (
        sa.Column(
            "proposal_contract_version",
            sa.String(length=10),
            nullable=False,
            server_default="1",
        ),
        sa.Column("intent_id", sa.String(length=36), nullable=True),
        sa.Column(
            "workflow_mode",
            sa.String(length=40),
            nullable=False,
            server_default="legacy_custom",
        ),
        sa.Column(
            "scope_type",
            sa.String(length=20),
            nullable=False,
            server_default="project",
        ),
        sa.Column("scope_id", sa.String(length=36), nullable=True),
        sa.Column(
            "result_kind",
            sa.String(length=20),
            nullable=False,
            server_default="changes",
        ),
        sa.Column(
            "no_change_reason",
            sa.String(length=40),
            nullable=False,
            server_default="",
        ),
        sa.Column(
            "scan_summary_json",
            sa.Text(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "top_level_evidence_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "top_level_source_references_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "fingerprint_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("regenerates_proposal_id", sa.String(length=36), nullable=True),
        sa.Column("superseded_by_proposal_id", sa.String(length=36), nullable=True),
    ),
    "proposal_operations": (
        sa.Column(
            "basis",
            sa.String(length=30),
            nullable=False,
            server_default="",
        ),
    ),
}


INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (
        "journal_entries",
        "ux_journal_entries_origin",
        ("project_id", "task_id", "origin_key"),
        True,
    ),
    (
        "journal_entries",
        "ix_journal_project_task_occurred",
        ("project_id", "task_id", "occurred_at"),
        False,
    ),
    ("proposals", "ix_proposals_intent_id", ("intent_id",), False),
    (
        "proposals",
        "ix_proposals_project_status_created",
        ("project_id", "status", "created_at"),
        False,
    ),
    (
        "proposals",
        "ix_proposals_project_mode_scope",
        ("project_id", "workflow_mode", "scope_type", "scope_id"),
        False,
    ),
    (
        "proposals",
        "ix_proposals_project_result_created",
        ("project_id", "result_kind", "created_at"),
        False,
    ),
)


def _columns(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _unique_shapes(inspector: sa.Inspector, table_name: str) -> set[tuple[str, ...]]:
    shapes = {
        tuple(str(value) for value in item.get("column_names") or ())
        for item in inspector.get_unique_constraints(table_name)
    }
    shapes.update(
        tuple(str(value) for value in item.get("column_names") or ())
        for item in inspector.get_indexes(table_name)
        if item.get("unique")
    )
    return {shape for shape in shapes if shape}


def _index_shapes(
    inspector: sa.Inspector,
    table_name: str,
    *,
    unique: bool,
) -> set[tuple[str, ...]]:
    return {
        tuple(str(value) for value in item.get("column_names") or ())
        for item in inspector.get_indexes(table_name)
        if bool(item.get("unique")) is unique
        and item.get("dialect_options", {}).get("sqlite_where") is None
    }


def _add_missing_columns(bind: sa.Connection) -> None:
    inspector = inspect(bind)
    for table_name, definitions in ADDITIVE_COLUMNS.items():
        existing = _columns(inspector, table_name)
        for definition in definitions:
            if definition.name in existing:
                continue
            op.add_column(table_name, definition)
            inspector = inspect(bind)
            existing = _columns(inspector, table_name)


def _source_schema_is_v0005(inspector: sa.Inspector) -> bool:
    return (
        "source_root_id" in _columns(inspector, "source_references")
        and _unique_shapes(inspector, "source_references") == NEW_SOURCE_UNIQUE
    )


def _ensure_project_artifact_roots(bind: sa.Connection) -> None:
    """Repair the enrollment invariant before assigning source-root identities."""

    missing = bind.execute(
        sa.text(
            """
            SELECT p.id, p.root_path
            FROM projects AS p
            WHERE NOT EXISTS (
                SELECT 1 FROM artifact_roots AS ar
                WHERE ar.project_id = p.id AND ar.is_project_root = 1
            )
            ORDER BY p.id
            """
        )
    ).mappings()
    now = datetime.now(timezone.utc)
    for project in missing:
        matching_root_id = bind.scalar(
            sa.text(
                "SELECT id FROM artifact_roots "
                "WHERE project_id=:project_id AND root_path=:root_path "
                "ORDER BY id LIMIT 1"
            ),
            {
                "project_id": project["id"],
                "root_path": project["root_path"],
            },
        )
        if matching_root_id is not None:
            bind.execute(
                sa.text(
                    "UPDATE artifact_roots SET is_project_root=1 "
                    "WHERE id=:root_id"
                ),
                {"root_id": matching_root_id},
            )
            continue
        bind.execute(
            sa.text(
                """
                INSERT INTO artifact_roots
                    (id, project_id, alias, root_path, is_project_root,
                     entity_version, created_at)
                VALUES (:id, :project_id, 'Project root', :root_path, 1, 1, :created_at)
                """
            ),
            {
                "id": str(uuid4()),
                "project_id": project["id"],
                "root_path": project["root_path"],
                "created_at": now,
            },
        )


def _rebuild_source_references(bind: sa.Connection) -> None:
    inspector = inspect(bind)
    if _source_schema_is_v0005(inspector):
        return
    if _unique_shapes(inspector, "source_references") != OLD_SOURCE_UNIQUE:
        raise RuntimeError(
            "Cannot migrate source_references with an unknown identity constraint"
        )
    tables = set(inspector.get_table_names())
    association_rows: list[dict[str, object]] = []
    if "task_source_references" in tables:
        association_rows = [
            dict(row)
            for row in bind.execute(
                sa.text(
                    "SELECT id, project_id, task_id, source_reference_id, created_at "
                    "FROM task_source_references ORDER BY id"
                )
            ).mappings()
        ]
        op.drop_table("task_source_references")

    temporary = "source_references_v0005_rebuild"
    if temporary in tables:
        raise RuntimeError(
            "Cannot resume revision 0005 with an unexpected source-reference rebuild table"
        )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE {temporary} (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                project_id VARCHAR(36) NOT NULL,
                task_id VARCHAR(36),
                source_root_id VARCHAR(36),
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
    root_expression = (
        "coalesce(source_root_id, "
        "(SELECT ar.id FROM artifact_roots AS ar "
        "WHERE ar.project_id = source_references.project_id "
        "AND ar.is_project_root = 1 ORDER BY ar.id LIMIT 1))"
        if "source_root_id" in _columns(inspector, "source_references")
        else (
            "(SELECT ar.id FROM artifact_roots AS ar "
            "WHERE ar.project_id = source_references.project_id "
            "AND ar.is_project_root = 1 ORDER BY ar.id LIMIT 1)"
        )
    )
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {temporary}
                (id, project_id, task_id, source_root_id, source_path, anchor,
                 opaque_key, fingerprint, imported_at)
            SELECT id, project_id, task_id, {root_expression}, source_path,
                   anchor, opaque_key, fingerprint, imported_at
            FROM source_references
            """
        )
    )
    op.drop_table("source_references")
    op.rename_table(temporary, "source_references")
    op.create_index(
        "ux_source_references_identity_v2",
        "source_references",
        ["project_id", "source_root_id", "source_path", "anchor", "opaque_key"],
        unique=True,
    )

    if association_rows:
        TASK_SOURCE_REFERENCES_TABLE.create(bind=bind)
        bind.execute(TASK_SOURCE_REFERENCES_TABLE.insert(), association_rows)


def _create_new_tables(bind: sa.Connection) -> None:
    tables = set(inspect(bind).get_table_names())
    for table in (
        PLANNING_PROFILES_TABLE,
        AGENT_INTENTS_TABLE,
        TASK_SOURCE_REFERENCES_TABLE,
    ):
        if table.name not in tables:
            table.create(bind=bind)
            tables.add(table.name)


def _ensure_indexes(bind: sa.Connection) -> None:
    inspector = inspect(bind)
    for table_name, index_name, columns, unique in INDEXES:
        shapes = (
            _unique_shapes(inspector, table_name)
            if unique
            else _index_shapes(inspector, table_name, unique=False)
        )
        if columns in shapes:
            continue
        names = {str(item["name"]) for item in inspector.get_indexes(table_name)}
        if index_name in names:
            raise RuntimeError(
                f"Cannot adopt malformed revision-0005 index: {index_name}"
            )
        op.create_index(index_name, table_name, list(columns), unique=unique)
        inspector = inspect(bind)


def _backfill(bind: sa.Connection, *, schema_was_complete: bool) -> None:
    now = datetime.now(timezone.utc)
    bind.execute(
        sa.text(
            """
            INSERT OR IGNORE INTO planning_profiles (
                project_id, task_granularity, max_nesting_depth,
                planning_horizon, inference_policy,
                max_new_tasks_per_proposal, preferred_pipeline_names_json,
                terminology_notes, additional_instructions,
                protected_pipeline_ids_json, protected_task_ids_json,
                entity_version, created_at, updated_at
            )
            SELECT id, 'balanced', 3, 'current_milestone', 'cautious_gaps',
                   30, '[]', '', '', '[]', '[]', 1, :now, :now
            FROM projects
            """
        ),
        {"now": now},
    )
    # Outside artifact roots require a fresh, explicit v2 readable-root grant.
    bind.execute(
        sa.text(
            "UPDATE scan_policies SET readable_source_root_ids_json = '[]', "
            "allow_outside_sources = 0"
        )
    )

    rows = bind.execute(
        sa.text("SELECT id, content FROM journal_entries ORDER BY id")
    ).mappings()
    for row in rows:
        digest = hashlib.sha256(str(row["content"]).encode("utf-8")).hexdigest()
        bind.execute(
            sa.text(
                "UPDATE journal_entries SET content_sha256 = :digest WHERE id = :id"
            ),
            {"digest": digest, "id": row["id"]},
        )

    if not schema_was_complete:
        bind.execute(
            sa.text(
                """
                UPDATE proposals
                SET proposal_contract_version = '1',
                    intent_id = NULL,
                    workflow_mode = 'legacy_custom',
                    scope_type = 'project',
                    scope_id = NULL,
                    result_kind = 'changes',
                    no_change_reason = '',
                    scan_summary_json = '{}',
                    top_level_evidence_json = '[]',
                    top_level_source_references_json = '[]',
                    fingerprint_version = 1,
                    regenerates_proposal_id = NULL,
                    superseded_by_proposal_id = NULL
                """
            )
        )
        bind.execute(sa.text("UPDATE proposal_operations SET basis = ''"))

    existing_links = {
        (str(row["task_id"]), str(row["source_reference_id"]))
        for row in bind.execute(
            sa.text(
                "SELECT task_id, source_reference_id FROM task_source_references"
            )
        ).mappings()
    }
    source_rows = bind.execute(
        sa.text(
            "SELECT id, project_id, task_id FROM source_references "
            "WHERE task_id IS NOT NULL ORDER BY id"
        )
    ).mappings()
    for row in source_rows:
        identity = (str(row["task_id"]), str(row["id"]))
        if identity in existing_links:
            continue
        bind.execute(
            TASK_SOURCE_REFERENCES_TABLE.insert().values(
                id=str(uuid4()),
                project_id=str(row["project_id"]),
                task_id=identity[0],
                source_reference_id=identity[1],
                created_at=now,
            )
        )
        existing_links.add(identity)


def _schema_was_complete(inspector: sa.Inspector) -> bool:
    try:
        return validate_v0005_schema(inspector, require_complete=True)
    except RuntimeError:
        # The partial validator has already proven that every present object is
        # a recognized resumable shape, so a complete-validation failure here
        # means only that one or more owned objects are absent.
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    validate_v0005_partial_schema(inspector)
    schema_was_complete = _schema_was_complete(inspector)

    _add_missing_columns(bind)
    _ensure_project_artifact_roots(bind)
    _rebuild_source_references(bind)
    _create_new_tables(bind)
    _ensure_indexes(bind)
    _backfill(bind, schema_was_complete=schema_was_complete)

    inspector = inspect(bind)
    validate_v0005_schema(inspector, require_complete=True)
    violations = bind.execute(sa.text("PRAGMA foreign_key_check")).fetchall()
    if violations:
        raise RuntimeError("Guided-agent migration found foreign-key violations")


def downgrade() -> None:
    # Application downgrades are restored from the verified pre-migration
    # backup. Retaining v0005 data keeps an interrupted retry data preserving.
    pass
