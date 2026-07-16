"""Repair two columns omitted by the released pre-v1 create-all schema.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections import defaultdict
from importlib import import_module

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from research_monitor.migrations.schema_v0001 import (
    reflected_full_unique_shapes,
    validate_v0001_legacy_repair_table,
    validate_v0001_adopted_schema,
)
from research_monitor.migrations.schema_v0002 import (
    validate_v0002_graph_viewports,
)


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


SOURCE_REFERENCE_COLUMNS = {
    "id",
    "project_id",
    "task_id",
    "source_path",
    "anchor",
    "fingerprint",
    "imported_at",
}
TASK_EDGE_COLUMNS = {
    "id",
    "project_id",
    "source_id",
    "target_id",
    "edge_type",
    "waived_reason",
    "enabled",
    "entity_version",
    "deleted_at",
    "created_at",
}
SOURCE_IDENTITY = ("project_id", "source_path", "anchor", "opaque_key")
SOURCE_IDENTITY_INDEX = "ux_source_references_identity"


def _rebuild_search_index() -> None:
    """Repair revision-0001's fully derived objects using its frozen DDL."""

    revision_0001 = import_module(
        "research_monitor.migrations.versions.0001_initial_schema_and_fts"
    )
    revision_0001.rebuild_search_index()


def _columns(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table_name)}


def _require_legacy_shape(
    inspector: sa.Inspector,
    table_name: str,
    required_columns: set[str],
    repair_column: str,
) -> None:
    tables = set(inspector.get_table_names())
    if table_name not in tables:
        raise RuntimeError(f"Cannot repair missing Research Monitor table: {table_name}")
    existing = _columns(inspector, table_name)
    missing = required_columns - existing
    if missing:
        raise RuntimeError(
            f"Cannot repair incompatible table {table_name}; missing columns: "
            + ", ".join(sorted(missing))
        )
    # This migration owns only one additive repair per table. Presence of the
    # repair column is valid and makes a repeated/partially resumed run safe.
    if repair_column in existing:
        return


def _has_source_identity(inspector: sa.Inspector) -> bool:
    return SOURCE_IDENTITY in reflected_full_unique_shapes(
        inspector,
        "source_references",
    )


def _disambiguate_blank_legacy_identities(bind: sa.Connection) -> None:
    """Keep all old rows even when the unguarded legacy identity was repeated."""

    rows = bind.execute(
        sa.text(
            """
            SELECT
                sr.id, sr.project_id, sr.source_path, sr.anchor, sr.opaque_key,
                coalesce(t.user_key, '') AS user_key
            FROM source_references AS sr
            LEFT JOIN tasks AS t ON t.id = sr.task_id
            ORDER BY sr.project_id, sr.source_path, sr.anchor, sr.opaque_key, sr.id
            """
        )
    ).mappings()
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    used: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for raw in rows:
        row = dict(raw)
        group = (str(row["project_id"]), str(row["source_path"]), str(row["anchor"]))
        groups[group].append(row)
        used[group].add(str(row["opaque_key"]))

    for group, group_rows in groups.items():
        blank_rows = [row for row in group_rows if str(row["opaque_key"]) == ""]
        # Preserve the first row's historical blank identity. Only rows that
        # would otherwise violate the new exact-identity index need a value.
        for row in blank_rows[1:]:
            preferred = str(row["user_key"]).strip()
            candidate = preferred if preferred and preferred not in used[group] else ""
            if not candidate:
                candidate = f"legacy:{row['id']}"
            used[group].add(candidate)
            bind.execute(
                sa.text(
                    "UPDATE source_references SET opaque_key = :opaque_key WHERE id = :id"
                ),
                {"opaque_key": candidate, "id": row["id"]},
            )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    validate_v0001_adopted_schema(
        inspector,
        allow_partial_source_identity=True,
        require_known_additive_columns=True,
    )
    validate_v0002_graph_viewports(inspector, required=True)
    _require_legacy_shape(
        inspector,
        "source_references",
        SOURCE_REFERENCE_COLUMNS,
        "opaque_key",
    )
    _require_legacy_shape(
        inspector,
        "task_edges",
        TASK_EDGE_COLUMNS,
        "disabled_reason",
    )
    validate_v0001_legacy_repair_table(
        inspector,
        "source_references",
        allow_partial_source_identity=True,
    )
    validate_v0001_legacy_repair_table(
        inspector,
        "task_edges",
        require_disabled_batch_id=True,
    )

    if "opaque_key" not in _columns(inspector, "source_references"):
        op.add_column(
            "source_references",
            sa.Column(
                "opaque_key",
                sa.String(length=240),
                nullable=False,
                server_default="",
            ),
        )
        inspector = inspect(bind)

    if not _has_source_identity(inspector):
        _disambiguate_blank_legacy_identities(bind)
        op.create_index(
            SOURCE_IDENTITY_INDEX,
            "source_references",
            list(SOURCE_IDENTITY),
            unique=True,
        )
        inspector = inspect(bind)

    if "disabled_reason" not in _columns(inspector, "task_edges"):
        op.add_column(
            "task_edges",
            sa.Column("disabled_reason", sa.Text(), nullable=False, server_default=""),
        )

    inspector = inspect(bind)
    validate_v0001_legacy_repair_table(
        inspector,
        "source_references",
        require_repair_column=True,
    )
    validate_v0001_legacy_repair_table(
        inspector,
        "task_edges",
        require_repair_column=True,
        require_disabled_batch_id=True,
    )

    # Some pre-release databases were stamped at 0003 after create_all and
    # contain the complete relational schema but none of revision 0001's fully
    # derived FTS objects. Rebuilding is safe: the frozen helper validates a
    # pre-existing reserved table before touching it, replaces the complete
    # owned trigger set, and backfills all searchable entities. Current-head
    # databases never reach this migration repair and remain strictly validated.
    _rebuild_search_index()

    violations = bind.execute(sa.text("PRAGMA foreign_key_check")).fetchall()
    if violations:
        raise RuntimeError("Legacy compatibility migration found foreign-key violations")


def downgrade() -> None:
    # Never discard newly representable source identities or edge provenance.
    # Schema downgrades are unsupported by the application; retaining these
    # columns lets a subsequent upgrade remain idempotent and data preserving.
    pass
