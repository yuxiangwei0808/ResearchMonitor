from __future__ import annotations

import re

from sqlalchemy import Connection, inspect, text

from .migrations.schema_v0001 import validate_v0001_adopted_schema
from .migrations.schema_v0002 import validate_v0002_graph_viewports
from .models import Base


SEARCH_TABLE = "research_search"
SEARCH_COLUMNS = ("project_id", "entity_type", "entity_id", "title", "content")
SEARCH_TRIGGER_NAMES = frozenset(
    {
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
)
_FTS5_TABLE_DDL = re.compile(
    r'^\s*CREATE\s+VIRTUAL\s+TABLE\s+'
    r'(?:(?:"research_search")|(?:\[research_search\])|(?:`research_search`)|research_search)'
    r'\s+USING\s+fts5\s*\(',
    re.IGNORECASE,
)


def validate_current_schema(connection: Connection) -> None:
    """Reject a falsely stamped or partially damaged current-head database."""

    inspector = inspect(connection)
    reflected_tables = set(inspector.get_table_names())
    required_tables = set(Base.metadata.tables)
    missing_tables = sorted(required_tables - reflected_tables)
    if missing_tables:
        raise RuntimeError(
            "Current Research Monitor schema is missing ORM tables: "
            + ", ".join(missing_tables)
        )
    for table_name in sorted(required_tables):
        required_columns = set(Base.metadata.tables[table_name].columns.keys())
        reflected_columns = {
            str(column["name"]) for column in inspector.get_columns(table_name)
        }
        missing_columns = sorted(required_columns - reflected_columns)
        if missing_columns:
            raise RuntimeError(
                f"Current Research Monitor table {table_name} is missing ORM columns: "
                + ", ".join(missing_columns)
            )

    # Frozen validators also enforce the released constraints, indexes, and
    # additive migration columns rather than trusting the Alembic stamp alone.
    validate_v0001_adopted_schema(
        inspector,
        require_known_additive_columns=True,
    )
    validate_v0002_graph_viewports(inspector, required=True)

    search_row = connection.execute(
        text("SELECT type, sql FROM sqlite_master WHERE name = :name"),
        {"name": SEARCH_TABLE},
    ).mappings().first()
    search_sql = str(search_row["sql"] or "") if search_row is not None else ""
    if (
        search_row is None
        or search_row["type"] != "table"
        or _FTS5_TABLE_DDL.match(search_sql) is None
    ):
        raise RuntimeError(
            "Current Research Monitor schema is missing its FTS5 research_search table"
        )
    visible_columns = tuple(
        str(row["name"])
        for row in connection.execute(
            text(
                "SELECT name FROM pragma_table_xinfo(:table_name) "
                "WHERE hidden = 0 ORDER BY cid"
            ),
            {"table_name": SEARCH_TABLE},
        ).mappings()
    )
    if visible_columns != SEARCH_COLUMNS:
        raise RuntimeError(
            "Current Research Monitor FTS5 table has an incompatible visible-column shape"
        )

    owned_triggers = {
        str(row["name"])
        for row in connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'trigger' AND name LIKE 'rm_search_%'"
            )
        ).mappings()
    }
    if owned_triggers != SEARCH_TRIGGER_NAMES:
        missing = sorted(SEARCH_TRIGGER_NAMES - owned_triggers)
        unexpected = sorted(owned_triggers - SEARCH_TRIGGER_NAMES)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unexpected:
            details.append("unexpected " + ", ".join(unexpected))
        raise RuntimeError(
            "Current Research Monitor search trigger set is incompatible: "
            + "; ".join(details)
        )

    # A read-only MATCH query makes SQLite parse and open the FTS index without
    # adding validation rows or changing the monitor's semantic state.
    connection.scalar(
        text(
            f"SELECT count(*) FROM {SEARCH_TABLE} "
            f"WHERE {SEARCH_TABLE} MATCH :query"
        ),
        {"query": "__research_monitor_schema_validation__"},
    )
