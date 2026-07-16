"""Persist graph viewport state independently from semantic entities.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from research_monitor.migrations.schema_v0001 import (
    validate_v0001_adopted_schema,
)
from research_monitor.migrations.schema_v0002 import (
    GRAPH_VIEWPORT_TABLE,
    validate_v0002_graph_viewports,
)


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


TABLE = GRAPH_VIEWPORT_TABLE.name


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    validate_v0001_adopted_schema(
        inspector,
        allow_partial_source_identity=True,
    )
    if validate_v0002_graph_viewports(inspector, required=False):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("scope_id", sa.String(length=36), nullable=False, server_default="root"),
        sa.Column("x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("zoom", sa.Float(), nullable=False, server_default="1"),
        sa.Column("entity_version", sa.Integer(), nullable=False, server_default="1"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "scope_id", name="uq_graph_viewport_scope"),
    )
    validate_v0002_graph_viewports(inspect(bind), required=True)


def downgrade() -> None:
    bind = op.get_bind()
    if TABLE in inspect(bind).get_table_names():
        op.drop_table(TABLE)
