"""Frozen storage schema owned by Alembic revision 0005.

This snapshot deliberately depends only on earlier frozen schema helpers, not
on the live ORM. It validates complete current databases, safe partial 0005
adoption states, and the explicit current-ORM ``create_all`` compatibility
path without changing revision 0001's normal acceptance surface.
"""

from __future__ import annotations

import sqlalchemy as sa

from research_monitor.migrations.schema_v0001 import (
    V0001_KNOWN_ADDITIVE_COLUMNS,
    V0001_METADATA,
    reflected_full_unique_shapes,
    validate_frozen_table_structure,
)
from research_monitor.migrations.schema_v0002 import validate_v0002_graph_viewports


V0005_METADATA = sa.MetaData()

# Referenced tables whose complete shapes remain owned by prior revisions.
TASKS_STUB = sa.Table(
    "tasks",
    V0005_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
)

PROJECTS_TABLE = sa.Table(
    "projects",
    V0005_METADATA,
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
    sa.Column("last_agent_check_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)

SCAN_POLICIES_TABLE = sa.Table(
    "scan_policies",
    V0005_METADATA,
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
    sa.Column("readable_source_root_ids_json", sa.Text(), nullable=False),
    sa.Column("max_text_bytes", sa.Integer(), nullable=False),
    sa.Column("max_files_per_scan", sa.Integer(), nullable=False),
    sa.Column("max_total_text_bytes", sa.Integer(), nullable=False),
    sa.Column("allow_git_metadata", sa.Boolean(), nullable=False),
    sa.Column("git_history_limit", sa.Integer(), nullable=False),
    sa.Column("allow_outside_sources", sa.Boolean(), nullable=False),
    sa.Column("follow_symlinks", sa.Boolean(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
)

PLANNING_PROFILES_TABLE = sa.Table(
    "planning_profiles",
    V0005_METADATA,
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("task_granularity", sa.String(length=20), nullable=False),
    sa.Column("max_nesting_depth", sa.Integer(), nullable=False),
    sa.Column("planning_horizon", sa.String(length=30), nullable=False),
    sa.Column("inference_policy", sa.String(length=30), nullable=False),
    sa.Column("max_new_tasks_per_proposal", sa.Integer(), nullable=False),
    sa.Column("preferred_pipeline_names_json", sa.Text(), nullable=False),
    sa.Column("terminology_notes", sa.Text(), nullable=False),
    sa.Column("additional_instructions", sa.Text(), nullable=False),
    sa.Column("protected_pipeline_ids_json", sa.Text(), nullable=False),
    sa.Column("protected_task_ids_json", sa.Text(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
)

JOURNAL_ENTRIES_TABLE = sa.Table(
    "journal_entries",
    V0005_METADATA,
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
    sa.Column("origin_key", sa.String(length=240), nullable=True),
    sa.Column("content_sha256", sa.String(length=64), nullable=False),
    sa.Column("occurred_at", sa.DateTime(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.Column("deleted_at", sa.DateTime(), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
        "project_id",
        "task_id",
        "origin_key",
        name="uq_journal_project_task_origin",
    ),
    sa.Index(
        "ix_journal_project_task_occurred",
        "project_id",
        "task_id",
        "occurred_at",
    ),
)

SOURCE_REFERENCES_TABLE = sa.Table(
    "source_references",
    V0005_METADATA,
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
    # Logical approved-root identifier. It remains nullable and intentionally
    # has no database FK so old v1 proposal writers remain compatible while v2
    # validation enforces an approved root before guided persistence.
    sa.Column("source_root_id", sa.String(length=36), nullable=True),
    sa.Column("source_path", sa.Text(), nullable=False),
    sa.Column("anchor", sa.Text(), nullable=False),
    sa.Column("opaque_key", sa.String(length=240), nullable=False),
    sa.Column("fingerprint", sa.String(length=128), nullable=False),
    sa.Column("imported_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
        "project_id",
        "source_root_id",
        "source_path",
        "anchor",
        "opaque_key",
        name="uq_source_identity_v2",
    ),
)

TASK_SOURCE_REFERENCES_TABLE = sa.Table(
    "task_source_references",
    V0005_METADATA,
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
        "source_reference_id",
        sa.String(length=36),
        sa.ForeignKey("source_references.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
        "task_id",
        "source_reference_id",
        name="uq_task_source_reference",
    ),
    sa.Index(
        "ix_task_source_reference_project_task",
        "project_id",
        "task_id",
    ),
)

AGENT_INTENTS_TABLE = sa.Table(
    "agent_intents",
    V0005_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column("proposal_request_id", sa.String(length=36), nullable=False),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("issued_semantic_revision", sa.Integer(), nullable=False),
    sa.Column("planning_profile_version", sa.Integer(), nullable=False),
    sa.Column("workflow_mode", sa.String(length=40), nullable=False),
    sa.Column("scope_type", sa.String(length=20), nullable=False),
    sa.Column("scope_id", sa.String(length=36), nullable=True),
    sa.Column("instructions", sa.Text(), nullable=False),
    sa.Column("allow_completion", sa.Boolean(), nullable=False),
    sa.Column("artifact_locators_json", sa.Text(), nullable=False),
    sa.Column("regenerates_proposal_id", sa.String(length=36), nullable=True),
    sa.Column("superseded_by_intent_id", sa.String(length=36), nullable=True),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
    sa.Column("consumed_proposal_id", sa.String(length=36), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.UniqueConstraint(
        "proposal_request_id",
        name="uq_agent_intent_proposal_request",
    ),
    sa.Index("ix_agent_intent_project_created", "project_id", "created_at"),
    sa.Index("ix_agent_intent_project_expires", "project_id", "expires_at"),
)

PROPOSALS_TABLE = sa.Table(
    "proposals",
    V0005_METADATA,
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
    sa.Column("proposal_contract_version", sa.String(length=10), nullable=False),
    sa.Column("intent_id", sa.String(length=36), nullable=True),
    sa.Column("workflow_mode", sa.String(length=40), nullable=False),
    sa.Column("scope_type", sa.String(length=20), nullable=False),
    sa.Column("scope_id", sa.String(length=36), nullable=True),
    sa.Column("result_kind", sa.String(length=20), nullable=False),
    sa.Column("no_change_reason", sa.String(length=40), nullable=False),
    sa.Column("scan_summary_json", sa.Text(), nullable=False),
    sa.Column("top_level_evidence_json", sa.Text(), nullable=False),
    sa.Column("top_level_source_references_json", sa.Text(), nullable=False),
    sa.Column("fingerprint_version", sa.Integer(), nullable=False),
    sa.Column("regenerates_proposal_id", sa.String(length=36), nullable=True),
    sa.Column("superseded_by_proposal_id", sa.String(length=36), nullable=True),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("closed_at", sa.DateTime(), nullable=True),
    sa.Index("ix_proposals_fingerprint", "fingerprint"),
    sa.Index("ix_proposals_intent_id", "intent_id"),
    sa.Index(
        "ix_proposals_project_status_created",
        "project_id",
        "status",
        "created_at",
    ),
    sa.Index(
        "ix_proposals_project_mode_scope",
        "project_id",
        "workflow_mode",
        "scope_type",
        "scope_id",
    ),
    sa.Index(
        "ix_proposals_project_result_created",
        "project_id",
        "result_kind",
        "created_at",
    ),
)

PROPOSAL_OPERATIONS_TABLE = sa.Table(
    "proposal_operations",
    V0005_METADATA,
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
    sa.Column("basis", sa.String(length=30), nullable=False),
)


V0005_CHANGED_TABLES = {
    table.name: table
    for table in (
        PROJECTS_TABLE,
        SCAN_POLICIES_TABLE,
        JOURNAL_ENTRIES_TABLE,
        SOURCE_REFERENCES_TABLE,
        PROPOSALS_TABLE,
        PROPOSAL_OPERATIONS_TABLE,
    )
}
V0005_NEW_TABLES = {
    table.name: table
    for table in (
        PLANNING_PROFILES_TABLE,
        TASK_SOURCE_REFERENCES_TABLE,
        AGENT_INTENTS_TABLE,
    )
}
V0005_ADDITIVE_COLUMNS: dict[str, dict[str, tuple[object, bool]]] = {
    table_name: {
        column.name: (column.type, bool(column.nullable))
        for column in table.columns
        if column.name not in V0001_METADATA.tables[table_name].columns
    }
    for table_name, table in V0005_CHANGED_TABLES.items()
}

OLD_SOURCE_UNIQUE = {("project_id", "source_path", "anchor", "opaque_key")}
NEW_SOURCE_UNIQUE = {
    ("project_id", "source_root_id", "source_path", "anchor", "opaque_key")
}
OLD_JOURNAL_UNIQUE: set[tuple[str, ...]] = set()
NEW_JOURNAL_UNIQUE = {("project_id", "task_id", "origin_key")}
OLD_PROPOSAL_UNIQUE = {("request_id",)}
NEW_PROPOSAL_UNIQUE = {("request_id",)}


class _InspectorProjection:
    """Expose an intentional historical view without weakening old validators."""

    def __init__(
        self,
        inspector: sa.Inspector,
        *,
        hidden_columns: dict[str, set[str]] | None = None,
        unique_shapes: dict[str, set[tuple[str, ...]]] | None = None,
    ) -> None:
        self._inspector = inspector
        self.bind = inspector.bind
        self._hidden_columns = hidden_columns or {}
        self._unique_shapes = unique_shapes or {}

    def __getattr__(self, name: str) -> object:
        return getattr(self._inspector, name)

    def get_columns(
        self, table_name: str, *args: object, **kwargs: object
    ) -> list[dict[str, object]]:
        hidden = self._hidden_columns.get(table_name, set())
        return [
            dict(column)
            for column in self._inspector.get_columns(table_name, *args, **kwargs)
            if str(column["name"]) not in hidden
        ]

    def get_unique_constraints(
        self, table_name: str, *args: object, **kwargs: object
    ) -> list[dict[str, object]]:
        shapes = self._unique_shapes.get(table_name)
        if shapes is None:
            return list(
                self._inspector.get_unique_constraints(table_name, *args, **kwargs)
            )
        return [
            {"name": None, "column_names": list(shape)}
            for shape in sorted(shapes)
        ]

    def get_indexes(
        self, table_name: str, *args: object, **kwargs: object
    ) -> list[dict[str, object]]:
        shapes = self._unique_shapes.get(table_name)
        if shapes is None:
            return list(self._inspector.get_indexes(table_name, *args, **kwargs))
        indexes = [
            dict(index)
            for index in self._inspector.get_indexes(table_name, *args, **kwargs)
            if not bool(index.get("unique"))
        ]
        indexes.extend(
            {
                "name": f"__frozen_projection_unique_{position}",
                "column_names": list(shape),
                "unique": True,
                "dialect_options": {},
            }
            for position, shape in enumerate(sorted(shapes))
        )
        return indexes


def project_v0004_inspector(inspector: sa.Inspector) -> sa.Inspector:
    """Project a complete v0005 database onto the released v0004 contract.

    Historical validators stay byte-for-byte frozen. This view hides only
    objects intentionally owned by revision 0005; the real shapes are validated
    immediately afterward by the frozen v0005 contract.
    """

    return _InspectorProjection(
        inspector,
        hidden_columns={
            table_name: set(columns)
            for table_name, columns in V0005_ADDITIVE_COLUMNS.items()
        },
        unique_shapes={
            "journal_entries": OLD_JOURNAL_UNIQUE,
            "source_references": OLD_SOURCE_UNIQUE,
            "proposals": OLD_PROPOSAL_UNIQUE,
        },
    )  # type: ignore[return-value]


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def has_any_v0005_storage(inspector: sa.Inspector) -> bool:
    """Return whether any revision-0005-owned object is already present."""

    tables = set(inspector.get_table_names())
    if tables & set(V0005_NEW_TABLES):
        return True
    for table_name, additions in V0005_ADDITIVE_COLUMNS.items():
        if table_name in tables and _column_names(inspector, table_name) & set(additions):
            return True
    return False


def _validate_unchanged_v0001_tables(inspector: sa.Inspector) -> None:
    for table_name, table in V0001_METADATA.tables.items():
        if table_name in V0005_CHANGED_TABLES:
            continue
        additive = V0001_KNOWN_ADDITIVE_COLUMNS.get(table_name) or {}
        validate_frozen_table_structure(
            inspector,
            table,
            allowed_extra_columns=additive,
            required_extra_columns=set(additive),
        )


def validate_v0005_partial_schema(inspector: sa.Inspector) -> None:
    """Validate released 0004 plus any safely resumable 0005 additions."""

    validate_v0002_graph_viewports(inspector, required=True)
    _validate_unchanged_v0001_tables(inspector)

    permitted_uniques = {
        "journal_entries": [OLD_JOURNAL_UNIQUE, NEW_JOURNAL_UNIQUE],
        "source_references": [OLD_SOURCE_UNIQUE, NEW_SOURCE_UNIQUE],
        "proposals": [OLD_PROPOSAL_UNIQUE, NEW_PROPOSAL_UNIQUE],
    }
    historical_uniques = {
        "journal_entries": OLD_JOURNAL_UNIQUE,
        "source_references": OLD_SOURCE_UNIQUE,
        "proposals": OLD_PROPOSAL_UNIQUE,
    }
    for table_name in V0005_CHANGED_TABLES:
        allowed_unique = permitted_uniques.get(table_name)
        if (
            allowed_unique is not None
            and reflected_full_unique_shapes(inspector, table_name)
            not in allowed_unique
        ):
            raise RuntimeError(
                f"Cannot adopt incompatible table {table_name}; unique "
                "constraint/index shape does not match revision 0005"
            )
        historical_unique = historical_uniques.get(table_name)
        projected = _InspectorProjection(
            inspector,
            unique_shapes=(
                {table_name: historical_unique}
                if historical_unique is not None
                else {}
            ),
        )
        validate_frozen_table_structure(
            projected,  # type: ignore[arg-type]
            V0001_METADATA.tables[table_name],
            allowed_extra_columns=V0005_ADDITIVE_COLUMNS[table_name],
        )

    tables = set(inspector.get_table_names())
    for table_name, table in V0005_NEW_TABLES.items():
        if table_name in tables:
            validate_frozen_table_structure(inspector, table)


def validate_v0005_schema(
    inspector: sa.Inspector,
    *,
    require_complete: bool,
) -> bool:
    """Validate the complete frozen v0005 schema.

    Returns ``False`` only when no 0005 object exists and completeness was not
    requested. Any partial presence is validated and then reported as
    incomplete rather than treated as a legacy schema.
    """

    if not has_any_v0005_storage(inspector):
        if require_complete:
            raise RuntimeError("Current Research Monitor schema is missing revision 0005")
        return False

    if not require_complete:
        validate_v0005_partial_schema(inspector)
        return False

    validate_v0002_graph_viewports(inspector, required=True)
    _validate_unchanged_v0001_tables(inspector)
    tables = set(inspector.get_table_names())
    missing = sorted(set(V0005_NEW_TABLES) - tables)
    if missing:
        raise RuntimeError(
            "Current Research Monitor schema is missing revision-0005 tables: "
            + ", ".join(missing)
        )
    for table in V0005_CHANGED_TABLES.values():
        validate_frozen_table_structure(inspector, table)
    for table in V0005_NEW_TABLES.values():
        validate_frozen_table_structure(inspector, table)
    return True
