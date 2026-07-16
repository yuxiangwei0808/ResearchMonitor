"""Frozen relational schema owned by Alembic revision 0001.

This module is deliberately independent of the application's declarative ORM.
Historical migrations must not change when a live model gains a table, column,
constraint, or index. Keep this metadata immutable; later schema changes belong
in later Alembic revisions.
"""

from __future__ import annotations

import sqlalchemy as sa


V0001_METADATA = sa.MetaData()


sa.Table(
    "projects",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("name", sa.String(length=240), nullable=False),
    sa.Column("root_path", sa.Text(), nullable=False, unique=True),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("research_goal", sa.Text(), nullable=False),
    sa.Column("success_criteria", sa.Text(), nullable=False),
    sa.Column("color", sa.String(length=32), nullable=False),
    sa.Column("semantic_revision", sa.Integer(), nullable=False),
    sa.Column("layout_revision", sa.Integer(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("archived_at", sa.DateTime(), nullable=True),
    sa.Column("trashed_at", sa.DateTime(), nullable=True),
    sa.Column("last_manual_update_at", sa.DateTime(), nullable=True),
    sa.Column("last_proposal_at", sa.DateTime(), nullable=True),
    sa.Column("last_agent_sync_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)

sa.Table(
    "scan_policies",
    V0001_METADATA,
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("preferred_sources_json", sa.Text(), nullable=False),
    sa.Column("include_globs_json", sa.Text(), nullable=False),
    sa.Column("exclude_globs_json", sa.Text(), nullable=False),
    sa.Column("sensitive_patterns_json", sa.Text(), nullable=False),
    sa.Column("max_text_bytes", sa.Integer(), nullable=False),
    sa.Column("allow_git_metadata", sa.Boolean(), nullable=False),
    sa.Column("git_history_limit", sa.Integer(), nullable=False),
    sa.Column("allow_outside_sources", sa.Boolean(), nullable=False),
    sa.Column("follow_symlinks", sa.Boolean(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
)

sa.Table(
    "artifact_roots",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("alias", sa.String(length=120), nullable=False),
    sa.Column("root_path", sa.Text(), nullable=False),
    sa.Column("is_project_root", sa.Boolean(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("project_id", "root_path"),
)

sa.Table(
    "pipelines",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("title", sa.String(length=500), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("flow_mode", sa.String(length=20), nullable=False),
    sa.Column("order_index", sa.Float(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("archived_at", sa.DateTime(), nullable=True),
    sa.Column("deleted_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.Index("ix_pipeline_project_order", "project_id", "order_index"),
)

sa.Table(
    "tasks",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "pipeline_id",
        sa.String(length=36),
        sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "parent_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column("user_key", sa.String(length=240), nullable=True),
    sa.Column("kind", sa.String(length=20), nullable=False),
    sa.Column("title", sa.String(length=800), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("status", sa.String(length=30), nullable=False),
    sa.Column("outcome", sa.String(length=30), nullable=False),
    sa.Column("priority", sa.String(length=30), nullable=False),
    sa.Column("labels_json", sa.Text(), nullable=False),
    sa.Column("target_date", sa.String(length=10), nullable=True),
    sa.Column("order_index", sa.Float(), nullable=False),
    sa.Column("completion_criteria", sa.Text(), nullable=False),
    sa.Column("blocker_reason", sa.Text(), nullable=False),
    sa.Column("completion_summary", sa.Text(), nullable=False),
    sa.Column("completion_actor", sa.String(length=240), nullable=False),
    sa.Column("completion_source", sa.String(length=240), nullable=False),
    sa.Column("completion_override_reason", sa.Text(), nullable=False),
    sa.Column("completion_provenance", sa.String(length=30), nullable=False),
    sa.Column("child_flow_mode", sa.String(length=20), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("completed_at", sa.DateTime(), nullable=True),
    sa.Column("deleted_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("project_id", "user_key", name="uq_task_project_user_key"),
    sa.Index(
        "ix_task_project_pipeline_parent_order",
        "project_id",
        "pipeline_id",
        "parent_id",
        "order_index",
    ),
)

sa.Table(
    "task_edges",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "source_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "target_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("edge_type", sa.String(length=20), nullable=False),
    sa.Column("waived_reason", sa.Text(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("disabled_reason", sa.Text(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("deleted_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("project_id", "source_id", "target_id", "edge_type"),
)

sa.Table(
    "journal_entries",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "task_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("entry_type", sa.String(length=30), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("occurred_at", sa.DateTime(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("deleted_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)

sa.Table(
    "artifacts",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "root_id",
        sa.String(length=36),
        sa.ForeignKey("artifact_roots.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    sa.Column("locator_type", sa.String(length=20), nullable=False),
    sa.Column("locator", sa.Text(), nullable=False),
    sa.Column("provider", sa.String(length=120), nullable=False),
    sa.Column("label", sa.String(length=500), nullable=False),
    sa.Column("notes", sa.Text(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("deleted_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("project_id", "root_id", "locator", name="uq_artifact_locator"),
)

sa.Table(
    "task_artifacts",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "task_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "artifact_id",
        sa.String(length=36),
        sa.ForeignKey("artifacts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("role", sa.String(length=30), nullable=False),
    sa.Column("notes", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint("task_id", "artifact_id", "role"),
)

sa.Table(
    "task_layouts",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "task_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("scope_id", sa.String(length=36), nullable=False),
    sa.Column("x", sa.Float(), nullable=False),
    sa.Column("y", sa.Float(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.UniqueConstraint("project_id", "task_id", "scope_id"),
)

sa.Table(
    "source_references",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "task_id",
        sa.String(length=36),
        sa.ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=True,
    ),
    sa.Column("source_path", sa.Text(), nullable=False),
    sa.Column("anchor", sa.Text(), nullable=False),
    sa.Column("opaque_key", sa.String(length=240), nullable=False),
    sa.Column("fingerprint", sa.String(length=128), nullable=False),
    sa.Column("imported_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
        "project_id",
        "source_path",
        "anchor",
        "opaque_key",
        name="uq_source_identity",
    ),
)

sa.Table(
    "proposals",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("request_id", sa.String(length=36), nullable=False, unique=True),
    sa.Column("base_semantic_revision", sa.Integer(), nullable=False),
    sa.Column("summary", sa.String(length=800), nullable=False),
    sa.Column("rationale", sa.Text(), nullable=False),
    sa.Column("status", sa.String(length=30), nullable=False),
    sa.Column("fingerprint", sa.String(length=128), nullable=False),
    sa.Column("actor_label", sa.String(length=240), nullable=False),
    sa.Column("rejection_reason", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("closed_at", sa.DateTime(), nullable=True),
    sa.Index("ix_proposals_fingerprint", "fingerprint"),
)

sa.Table(
    "proposal_operations",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "proposal_id",
        sa.String(length=36),
        sa.ForeignKey("proposals.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("operation_type", sa.String(length=80), nullable=False),
    sa.Column("operation_json", sa.Text(), nullable=False),
    sa.Column("atomic_group_id", sa.String(length=36), nullable=True),
    sa.Column("prerequisites_json", sa.Text(), nullable=False),
    sa.Column("rationale", sa.Text(), nullable=False),
    sa.Column("confidence", sa.Float(), nullable=True),
    sa.Column("evidence_json", sa.Text(), nullable=False),
    sa.Column("source_references_json", sa.Text(), nullable=False),
    sa.Column("disposition", sa.String(length=20), nullable=False),
)

sa.Table(
    "audit_events",
    V0001_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("sequence", sa.Integer(), nullable=False),
    sa.Column("actor_type", sa.String(length=30), nullable=False),
    sa.Column("actor_label", sa.String(length=240), nullable=False),
    sa.Column("action", sa.String(length=100), nullable=False),
    sa.Column("entity_type", sa.String(length=80), nullable=False),
    sa.Column("entity_id", sa.String(length=36), nullable=False),
    sa.Column("before_json", sa.Text(), nullable=False),
    sa.Column("after_json", sa.Text(), nullable=False),
    sa.Column("request_id", sa.String(length=36), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index("ix_audit_events_sequence", "sequence"),
)

sa.Table(
    "outbox_events",
    V0001_METADATA,
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("project_id", sa.String(length=36), nullable=False),
    sa.Column("event_type", sa.String(length=80), nullable=False),
    sa.Column("payload_json", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index("ix_outbox_events_project_id", "project_id"),
)

sa.Table(
    "idempotency_records",
    V0001_METADATA,
    sa.Column("request_id", sa.String(length=36), primary_key=True),
    sa.Column("project_id", sa.String(length=36), nullable=False),
    sa.Column("action", sa.String(length=80), nullable=False),
    sa.Column("response_json", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Index("ix_idempotency_records_project_id", "project_id"),
)

sa.Table(
    "schema_versions",
    V0001_METADATA,
    sa.Column("version", sa.Integer(), primary_key=True),
    sa.Column("applied_at", sa.DateTime(), nullable=False),
)


V0001_TABLE_NAMES = frozenset(V0001_METADATA.tables)


# These are the only two column omissions present in the released pre-Alembic
# create-all schema. Keep the structural verifier beside the frozen metadata:
# revisions 0001 and 0004 must agree about exactly which legacy shape is safe to
# adopt and repair.
V0001_LEGACY_REPAIR_COLUMNS = {
    "source_references": "opaque_key",
    "task_edges": "disabled_reason",
}

# Pre-Alembic databases may already contain these later additive columns when
# a previous application build used declarative create_all. They are optional
# during 0001 adoption, but when present must have exactly the shape introduced
# by revision 0003.
V0001_KNOWN_ADDITIVE_COLUMNS: dict[str, dict[str, tuple[object, bool]]] = {
    "pipelines": {"deletion_batch_id": (sa.String(length=36), True)},
    "tasks": {"deletion_batch_id": (sa.String(length=36), True)},
    "task_edges": {"disabled_batch_id": (sa.String(length=36), True)},
    "artifacts": {"validation_warning": (sa.Text(), False)},
}


def _type_signature(value: object) -> str:
    return "".join(str(value).upper().split())


def _reflected_column_shape(item: dict[str, object]) -> tuple[str, ...] | None:
    columns = item.get("column_names")
    if not isinstance(columns, (list, tuple)) or not columns:
        return None
    if any(not isinstance(column, str) or not column for column in columns):
        return None
    return tuple(columns)


def _reflected_index_is_partial(item: dict[str, object]) -> bool:
    dialect_options = item.get("dialect_options") or {}
    return (
        isinstance(dialect_options, dict)
        and dialect_options.get("sqlite_where") is not None
    )


def _reflected_full_index_shapes(
    inspector: sa.Inspector,
    table_name: str,
    *,
    unique: bool,
) -> set[tuple[str, ...]]:
    shapes: set[tuple[str, ...]] = set()
    for item in inspector.get_indexes(table_name):
        if bool(item.get("unique")) != unique or _reflected_index_is_partial(item):
            continue
        shape = _reflected_column_shape(item)
        if shape is not None:
            shapes.add(shape)
    return shapes


def reflected_full_unique_shapes(
    inspector: sa.Inspector,
    table_name: str,
) -> set[tuple[str, ...]]:
    """Return only unconditional unique constraints over concrete columns."""

    shapes = {
        shape
        for item in inspector.get_unique_constraints(table_name)
        if (shape := _reflected_column_shape(item)) is not None
    }
    return shapes | _reflected_full_index_shapes(
        inspector,
        table_name,
        unique=True,
    )


def _non_full_indexes(
    inspector: sa.Inspector,
    table_name: str,
) -> dict[str, bool]:
    """Return partial/expression indexes and whether each is unique.

    SQLAlchemy omits SQLite expression indexes from reflection and a partial
    index retains the same reflected column list as a full index. Consult
    SQLite metadata so neither can satisfy a frozen invariant.
    """

    def inspect_connection(connection: sa.Connection) -> dict[str, bool]:
        indexes = connection.execute(
            sa.text(
                "SELECT name, [unique] AS is_unique, origin, partial "
                "FROM pragma_index_list(:table_name)"
            ),
            {"table_name": table_name},
        ).mappings()
        invalid: dict[str, bool] = {}
        for index in indexes:
            if str(index["origin"]) == "pk":
                continue
            index_name = str(index["name"])
            key_columns = connection.execute(
                sa.text(
                    "SELECT cid, name, [key] AS is_key "
                    "FROM pragma_index_xinfo(:index_name) ORDER BY seqno"
                ),
                {"index_name": index_name},
            ).mappings()
            concrete_columns = [row for row in key_columns if row["is_key"]]
            if index["partial"] or not concrete_columns or any(
                int(row["cid"]) < 0 or not isinstance(row["name"], str)
                for row in concrete_columns
            ):
                invalid[index_name] = bool(index["is_unique"])
        return invalid

    bind = inspector.bind
    if hasattr(bind, "connect"):
        with bind.connect() as connection:
            return inspect_connection(connection)
    return inspect_connection(bind)


def _foreign_key_shapes(
    inspector: sa.Inspector, table_name: str
) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str]]:
    return {
        (
            tuple(str(column) for column in item.get("constrained_columns") or ()),
            str(item.get("referred_table") or ""),
            tuple(str(column) for column in item.get("referred_columns") or ()),
            str((item.get("options") or {}).get("ondelete") or "").upper(),
        )
        for item in inspector.get_foreign_keys(table_name)
    }


def _validate_reflected_column(
    table_name: str,
    column_name: str,
    reflected: dict[str, object],
    expected_type: object,
    expected_nullable: bool,
) -> None:
    if bool(reflected.get("nullable")) != expected_nullable:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; column {column_name} "
            "has unexpected nullability"
        )
    if _type_signature(reflected.get("type")) != _type_signature(expected_type):
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; column {column_name} "
            "has unexpected type"
        )


def validate_frozen_column_structure(
    inspector: sa.Inspector,
    table_name: str,
    column_name: str,
    expected_type: object,
    expected_nullable: bool,
) -> None:
    """Validate one already-present additive column without live ORM metadata."""

    columns = {
        str(column["name"]): column for column in inspector.get_columns(table_name)
    }
    if column_name not in columns:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; missing columns: "
            f"{column_name}"
        )
    _validate_reflected_column(
        table_name,
        column_name,
        columns[column_name],
        expected_type,
        expected_nullable,
    )


def validate_frozen_table_structure(
    inspector: sa.Inspector,
    table: sa.Table,
    *,
    allowed_missing_columns: set[str] | None = None,
    allowed_extra_columns: dict[str, tuple[object, bool]] | None = None,
    required_extra_columns: set[str] | None = None,
    allow_missing_unique_constraints: bool = False,
) -> None:
    """Validate integrity-bearing SQLite structure against frozen metadata.

    Constraint and index names are intentionally ignored because SQLite may
    expose equivalent unique constraints as autoindexes. Extra non-unique
    performance indexes are harmless and allowed; required frozen indexes must
    still exist. Extra PK, FK, or uniqueness behavior is rejected.
    """

    table_name = table.name
    if table_name not in set(inspector.get_table_names()):
        raise RuntimeError(f"Cannot adopt missing Research Monitor table: {table_name}")
    reflected_columns = {
        str(column["name"]): column for column in inspector.get_columns(table_name)
    }
    expected_names = set(table.columns.keys())
    allowed_missing = allowed_missing_columns or set()
    extras = allowed_extra_columns or {}
    missing = expected_names - set(reflected_columns)
    if not missing.issubset(allowed_missing):
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; missing columns: "
            + ", ".join(sorted(missing))
        )
    unexpected = set(reflected_columns) - expected_names - set(extras)
    if unexpected:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; unexpected columns: "
            + ", ".join(sorted(unexpected))
        )
    missing_extras = (required_extra_columns or set()) - set(reflected_columns)
    if missing_extras:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; missing columns: "
            + ", ".join(sorted(missing_extras))
        )

    for name, expected in table.columns.items():
        reflected = reflected_columns.get(name)
        if reflected is not None:
            _validate_reflected_column(
                table_name,
                name,
                reflected,
                expected.type,
                bool(expected.nullable),
            )
    for name, (expected_type, expected_nullable) in extras.items():
        reflected = reflected_columns.get(name)
        if reflected is not None:
            _validate_reflected_column(
                table_name,
                name,
                reflected,
                expected_type,
                expected_nullable,
            )

    expected_pk = tuple(column.name for column in table.primary_key.columns)
    actual_pk = tuple(
        str(column)
        for column in inspector.get_pk_constraint(table_name).get(
            "constrained_columns", ()
        )
    )
    if actual_pk != expected_pk:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; primary key shape "
            "does not match the frozen schema"
        )

    expected_fks = {
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
            str(constraint.ondelete or "").upper(),
        )
        for constraint in table.foreign_key_constraints
    }
    if _foreign_key_shapes(inspector, table_name) != expected_fks:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; foreign key shape "
            "does not match the frozen schema"
        )

    expected_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
        and all(column.name in reflected_columns for column in constraint.columns)
    }
    non_full_indexes = _non_full_indexes(inspector, table_name)
    if any(non_full_indexes.values()):
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; unique constraint/index "
            "shape does not match the frozen schema"
        )
    actual_unique = reflected_full_unique_shapes(inspector, table_name)
    permitted_unique = [expected_unique]
    if allow_missing_unique_constraints:
        permitted_unique.append(set())
    if actual_unique not in permitted_unique:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; unique constraint/index "
            "shape does not match the frozen schema"
        )

    expected_indexes = {
        tuple(column.name for column in index.columns)
        for index in table.indexes
        if not index.unique
    }
    actual_indexes = _reflected_full_index_shapes(
        inspector,
        table_name,
        unique=False,
    )
    missing_indexes = expected_indexes - actual_indexes
    if missing_indexes:
        rendered = ", ".join("(" + ", ".join(shape) + ")" for shape in sorted(missing_indexes))
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; missing required indexes: "
            + rendered
        )


def validate_v0001_adopted_schema(
    inspector: sa.Inspector,
    *,
    allow_partial_source_identity: bool = False,
    require_known_additive_columns: bool = False,
) -> None:
    """Validate every frozen v0001 table before an existing schema is stamped."""

    for table_name in sorted(V0001_TABLE_NAMES):
        repair_column = V0001_LEGACY_REPAIR_COLUMNS.get(table_name)
        additive_columns = V0001_KNOWN_ADDITIVE_COLUMNS.get(table_name) or {}
        validate_frozen_table_structure(
            inspector,
            V0001_METADATA.tables[table_name],
            allowed_missing_columns={repair_column} if repair_column else set(),
            allowed_extra_columns=additive_columns,
            required_extra_columns=(
                set(additive_columns) if require_known_additive_columns else set()
            ),
            allow_missing_unique_constraints=(
                allow_partial_source_identity and table_name == "source_references"
            ),
        )


def validate_v0001_legacy_repair_table(
    inspector: sa.Inspector,
    table_name: str,
    *,
    allow_partial_source_identity: bool = False,
    require_repair_column: bool = False,
    require_disabled_batch_id: bool = False,
) -> None:
    """Reject lookalike tables before adopting or repairing their schema.

    Column names alone are insufficient: a table without its foreign keys or
    uniqueness constraints passes ``PRAGMA foreign_key_check`` because SQLite
    has no constraints to check. This validates the exact integrity-bearing
    structure emitted by the released SQLAlchemy create-all schema. The sole
    accepted post-v0001 addition is ``task_edges.disabled_batch_id`` from 0003.
    """

    if table_name not in V0001_LEGACY_REPAIR_COLUMNS:
        raise RuntimeError(f"No released legacy repair shape for table {table_name}")
    table = V0001_METADATA.tables[table_name]
    repair_column = V0001_LEGACY_REPAIR_COLUMNS[table_name]
    reflected_columns = {
        str(column["name"]): column for column in inspector.get_columns(table_name)
    }
    expected_names = set(table.columns.keys())
    allowed_extra: set[str] = set()
    if table_name == "task_edges":
        allowed_extra.add("disabled_batch_id")
    missing = expected_names - set(reflected_columns)
    allowed_missing = set() if require_repair_column else {repair_column}
    if not missing.issubset(allowed_missing):
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; missing columns: "
            + ", ".join(sorted(missing))
        )
    unexpected = set(reflected_columns) - expected_names - allowed_extra
    if unexpected:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; unexpected columns: "
            + ", ".join(sorted(unexpected))
        )
    if require_disabled_batch_id and "disabled_batch_id" not in reflected_columns:
        raise RuntimeError(
            "Cannot repair incompatible table task_edges; missing columns: "
            "disabled_batch_id"
        )

    for name, expected in table.columns.items():
        reflected = reflected_columns.get(name)
        if reflected is None:
            continue
        if bool(reflected.get("nullable")) != bool(expected.nullable):
            raise RuntimeError(
                f"Cannot adopt incompatible table {table_name}; column {name} "
                "has unexpected nullability"
            )
        if _type_signature(reflected.get("type")) != _type_signature(expected.type):
            raise RuntimeError(
                f"Cannot adopt incompatible table {table_name}; column {name} "
                "has unexpected type"
            )
    if "disabled_batch_id" in reflected_columns:
        disabled_batch = reflected_columns["disabled_batch_id"]
        if not bool(disabled_batch.get("nullable")) or _type_signature(
            disabled_batch.get("type")
        ) != "VARCHAR(36)":
            raise RuntimeError(
                "Cannot adopt incompatible table task_edges; column "
                "disabled_batch_id has unexpected structure"
            )

    expected_pk = tuple(column.name for column in table.primary_key.columns)
    actual_pk = tuple(
        str(column)
        for column in inspector.get_pk_constraint(table_name).get(
            "constrained_columns", ()
        )
    )
    if actual_pk != expected_pk:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; primary key shape "
            "does not match the released schema"
        )

    expected_fks = {
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
            str(constraint.ondelete or "").upper(),
        )
        for constraint in table.foreign_key_constraints
    }
    if _foreign_key_shapes(inspector, table_name) != expected_fks:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; foreign key shape "
            "does not match the released schema"
        )

    expected_unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    if repair_column not in reflected_columns:
        expected_unique = {
            shape for shape in expected_unique if repair_column not in shape
        }
    non_full_indexes = _non_full_indexes(inspector, table_name)
    if any(non_full_indexes.values()):
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; unique constraint/index "
            "shape does not match the released schema"
        )
    actual_unique = reflected_full_unique_shapes(inspector, table_name)
    permitted_unique_shapes = [expected_unique]
    if (
        table_name == "source_references"
        and repair_column in reflected_columns
        and allow_partial_source_identity
    ):
        permitted_unique_shapes.append(set())
    if actual_unique not in permitted_unique_shapes:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; unique constraint/index "
            "shape does not match the released schema"
        )

    non_unique_indexes = {
        tuple(str(column) for column in item.get("column_names") or ())
        for item in inspector.get_indexes(table_name)
        if not item.get("unique")
    }
    if non_unique_indexes:
        raise RuntimeError(
            f"Cannot adopt incompatible table {table_name}; unexpected indexes"
        )
