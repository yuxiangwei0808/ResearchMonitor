"""Frozen revision-0001 definitions for the derived FTS5 search objects.

This module intentionally has no Alembic, SQLAlchemy, or application-model
imports. Revision 0001 and current-head validation share these immutable SQL
definitions so validation cannot drift from the schema the migration installs.
"""

from __future__ import annotations


SEARCH_TABLE = "research_search"
SEARCH_COLUMNS = ("project_id", "entity_type", "entity_id", "title", "content")
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

SEARCH_TRIGGER_SQL = {
    "rm_search_task_ai": f"""
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
    "rm_search_task_au": f"""
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
    "rm_search_task_ad": f"""
        CREATE TRIGGER rm_search_task_ad
        AFTER DELETE ON tasks
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'task' AND entity_id = old.id;
        END
        """,
    "rm_search_journal_ai": f"""
        CREATE TRIGGER rm_search_journal_ai
        AFTER INSERT ON journal_entries WHEN new.deleted_at IS NULL
        BEGIN
          INSERT INTO {SEARCH_TABLE}(project_id, entity_type, entity_id, title, content)
          VALUES (new.project_id, 'journal', new.id, new.entry_type, new.content);
        END
        """,
    "rm_search_journal_au": f"""
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
    "rm_search_journal_ad": f"""
        CREATE TRIGGER rm_search_journal_ad
        AFTER DELETE ON journal_entries
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'journal' AND entity_id = old.id;
        END
        """,
    "rm_search_artifact_ai": f"""
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
    "rm_search_artifact_au": f"""
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
    "rm_search_artifact_ad": f"""
        CREATE TRIGGER rm_search_artifact_ad
        AFTER DELETE ON artifacts
        BEGIN
          DELETE FROM {SEARCH_TABLE}
          WHERE entity_type = 'artifact' AND entity_id = old.id;
        END
        """,
}

SEARCH_TRIGGER_NAMES = tuple(SEARCH_TRIGGER_SQL)


def canonicalize_sql_whitespace(value: str) -> str:
    """Collapse SQL whitespace outside quoted values without changing tokens.

    SQLite omits surrounding whitespace and the final statement terminator in
    ``sqlite_master.sql``. It otherwise preserves submitted formatting. This
    accepts formatting-only differences while preserving case and every
    character inside quoted strings or identifiers, where whitespace matters.
    """

    source = value.strip()
    if source.endswith(";"):
        source = source[:-1].rstrip()

    output: list[str] = []
    quote: str | None = None
    pending_space = False
    index = 0
    while index < len(source):
        character = source[index]
        if quote is not None:
            output.append(character)
            if quote == "[":
                if character == "]":
                    quote = None
            elif character == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    output.append(source[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue

        if character.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space and output:
            output.append(" ")
        pending_space = False
        output.append(character)
        if character in {"'", '"', "`"}:
            quote = character
        elif character == "[":
            quote = "["
        index += 1

    return "".join(output)
