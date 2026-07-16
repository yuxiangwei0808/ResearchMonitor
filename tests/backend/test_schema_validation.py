from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_monitor.database import Database, DatabaseSchemaError
from research_monitor.migrations.fts_v0001 import (
    SEARCH_CREATE_SQL,
    SEARCH_TRIGGER_SQL,
    canonicalize_sql_whitespace,
)


def _initialize(path: Path) -> None:
    database = Database(path)
    try:
        database.initialize()
    finally:
        database.engine.dispose()


def _assert_schema_rejected(path: Path, message: str) -> None:
    database = Database(path)
    try:
        with pytest.raises(DatabaseSchemaError, match=message):
            database.initialize()
    finally:
        database.engine.dispose()


def test_current_head_rejects_same_name_altered_search_trigger_body(
    tmp_path: Path,
) -> None:
    path = tmp_path / "altered-trigger.db"
    _initialize(path)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TRIGGER rm_search_task_ai;
            CREATE TRIGGER rm_search_task_ai
            AFTER INSERT ON tasks WHEN new.deleted_at IS NULL
            BEGIN
              SELECT new.id;
            END;
            """
        )

    _assert_schema_rejected(path, "search trigger definition.*rm_search_task_ai")
def test_current_head_rejects_non_sqlite_unicode_whitespace_in_trigger(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unicode-whitespace-trigger.db"
    _initialize(path)
    trigger_name = "rm_search_task_ai"
    shadow_table = "\N{NO-BREAK SPACE}research_search"
    altered_trigger = SEARCH_TRIGGER_SQL[trigger_name].replace(
        "INTO research_search",
        f"INTO {shadow_table}",
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"""
            CREATE TABLE "{shadow_table}" (
                project_id TEXT,
                entity_type TEXT,
                entity_id TEXT,
                title TEXT,
                content TEXT
            )
            """
        )
        connection.execute(f"DROP TRIGGER {trigger_name}")
        connection.execute(altered_trigger)

    _assert_schema_rejected(path, f"search trigger definition.*{trigger_name}")




def test_current_head_rejects_missing_owned_search_trigger(tmp_path: Path) -> None:
    path = tmp_path / "missing-trigger.db"
    _initialize(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER rm_search_journal_ad")

    _assert_schema_rejected(path, "search trigger set.*missing rm_search_journal_ad")


def test_current_head_rejects_extra_owned_search_trigger(tmp_path: Path) -> None:
    path = tmp_path / "extra-trigger.db"
    _initialize(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TRIGGER rm_search_unexpected
            AFTER INSERT ON tasks
            BEGIN
              SELECT new.id;
            END
            """
        )

    _assert_schema_rejected(path, "search trigger set.*unexpected rm_search_unexpected")


def test_current_head_rejects_noncanonical_fts_tokenizer_with_same_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "altered-tokenizer.db"
    _initialize(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE research_search")
        connection.execute(
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

    _assert_schema_rejected(path, "FTS5 table declaration")


def test_current_head_accepts_whitespace_only_search_sql_formatting(
    tmp_path: Path,
) -> None:
    path = tmp_path / "formatted-search-sql.db"
    _initialize(path)
    trigger_name = "rm_search_task_ai"
    formatted_table = (
        "\n\t"
        + SEARCH_CREATE_SQL.replace("\n", "\n  \t")
        .replace("fts5(", "fts5  (")
        .replace(",", "  ,  ")
    )
    formatted_trigger = (
        "\n\t"
        + SEARCH_TRIGGER_SQL[trigger_name]
        .replace("AFTER INSERT", "AFTER  \n\t INSERT")
        .replace(" WHEN ", "\n  WHEN    ")
        .replace("trim(", "trim  (")
        .replace("research_search(", "research_search  (")
    )
    with sqlite3.connect(path) as connection:
        connection.execute(f"DROP TRIGGER {trigger_name}")
        connection.execute("DROP TABLE research_search")
        connection.execute(formatted_table)
        connection.execute(formatted_trigger)

    _initialize(path)


def test_sql_canonicalization_preserves_quoted_whitespace_and_case() -> None:
    assert canonicalize_sql_whitespace(
        " SELECT  'a  b' "
    ) == canonicalize_sql_whitespace("SELECT 'a  b'")
    assert canonicalize_sql_whitespace(
        "SELECT f ( value , 2 )"
    ) == canonicalize_sql_whitespace("SELECT f(value,2)")
    assert canonicalize_sql_whitespace(
        "SELECT 1 + 2"
    ) == canonicalize_sql_whitespace("SELECT 1+2")
    assert canonicalize_sql_whitespace("SELECT 'a  b'") != canonicalize_sql_whitespace(
        "SELECT 'a b'"
    )
    assert canonicalize_sql_whitespace("select 1") != canonicalize_sql_whitespace(
        "SELECT 1"
    )
    assert canonicalize_sql_whitespace("SELECT alpha") != canonicalize_sql_whitespace(
        "SELECT al pha"
    )
    assert canonicalize_sql_whitespace("SELECT 1||2") != canonicalize_sql_whitespace(
        "SELECT 1 | | 2"
    )
    assert canonicalize_sql_whitespace("SELECT alpha") != canonicalize_sql_whitespace(
        "SELECT\N{NO-BREAK SPACE}alpha"
    )
