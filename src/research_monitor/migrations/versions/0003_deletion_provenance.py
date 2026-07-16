"""Track structural deletion provenance and relink validation warnings.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from research_monitor.migrations.schema_v0001 import (
    validate_frozen_column_structure,
    validate_v0001_adopted_schema,
)
from research_monitor.migrations.schema_v0002 import (
    validate_v0002_graph_viewports,
)


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


COLUMNS = {
    "pipelines": ("deletion_batch_id", sa.String(length=36), True),
    "tasks": ("deletion_batch_id", sa.String(length=36), True),
    "task_edges": ("disabled_batch_id", sa.String(length=36), True),
    "artifacts": ("validation_warning", sa.Text(), False),
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    missing_tables = sorted(set(COLUMNS) - tables)
    if missing_tables:
        raise RuntimeError(
            "Cannot apply deletion-provenance migration; missing tables: "
            + ", ".join(missing_tables)
        )
    validate_v0001_adopted_schema(
        inspector,
        allow_partial_source_identity=True,
    )
    validate_v0002_graph_viewports(inspector, required=True)
    for table_name, (column_name, column_type, nullable) in COLUMNS.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        if column_name in existing:
            validate_frozen_column_structure(
                inspector,
                table_name,
                column_name,
                column_type,
                nullable,
            )
            continue
        if column_name == "validation_warning":
            op.add_column(
                table_name,
                sa.Column(column_name, column_type, nullable=False, server_default=""),
            )
        else:
            op.add_column(table_name, sa.Column(column_name, column_type, nullable=True))
        inspector = inspect(bind)
        validate_frozen_column_structure(
            inspector,
            table_name,
            column_name,
            column_type,
            nullable,
        )
    validate_v0001_adopted_schema(
        inspector,
        allow_partial_source_identity=True,
        require_known_additive_columns=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    for table_name, (column_name, _column_type, _nullable) in reversed(tuple(COLUMNS.items())):
        if table_name not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        if column_name in existing:
            op.drop_column(table_name, column_name)
            inspector = inspect(bind)
