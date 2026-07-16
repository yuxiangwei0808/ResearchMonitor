"""Frozen graph viewport schema owned by Alembic revision 0002.

Keep this module independent of the live ORM. Later revisions use the same
snapshot to reject falsely stamped or partially corrupted databases before
they advance the Alembic head.
"""

from __future__ import annotations

import sqlalchemy as sa

from research_monitor.migrations.schema_v0001 import (
    validate_frozen_table_structure,
)


V0002_METADATA = sa.MetaData()

# Resolve the viewport foreign key without importing current application
# models. Only the referenced key identity belongs in this local stub.
sa.Table(
    "projects",
    V0002_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
)

GRAPH_VIEWPORT_TABLE = sa.Table(
    "graph_viewports",
    V0002_METADATA,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
        "project_id",
        sa.String(length=36),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("scope_id", sa.String(length=36), nullable=False),
    sa.Column("x", sa.Float(), nullable=False),
    sa.Column("y", sa.Float(), nullable=False),
    sa.Column("zoom", sa.Float(), nullable=False),
    sa.Column("entity_version", sa.Integer(), nullable=False),
    sa.UniqueConstraint("project_id", "scope_id", name="uq_graph_viewport_scope"),
)


def validate_v0002_graph_viewports(
    inspector: sa.Inspector,
    *,
    required: bool,
) -> bool:
    """Validate the frozen 0002 table, returning whether it is present."""

    if GRAPH_VIEWPORT_TABLE.name not in set(inspector.get_table_names()):
        if required:
            raise RuntimeError(
                "Cannot adopt missing Research Monitor table: graph_viewports"
            )
        return False
    validate_frozen_table_structure(inspector, GRAPH_VIEWPORT_TABLE)
    return True
