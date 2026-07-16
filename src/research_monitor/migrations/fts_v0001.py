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
_SQLITE_WHITESPACE = " \t\n\f\r"


def canonicalize_sql_whitespace(value: str) -> str:
    """Return a quote-preserving token signature for generated SQLite SQL.

    SQLite omits surrounding whitespace and the final statement terminator in
    ``sqlite_master.sql``. It otherwise preserves submitted formatting. This
    accepts every outside-quote whitespace-only spelling, including optional
    spaces beside punctuation, while preserving case, token boundaries, and
    every character inside quoted strings or identifiers.

    Tokens are length-prefixed so two different token sequences cannot compare
    equal merely because a delimiter also appears inside a quoted value.
    """

    # SQLite's tokenizer recognizes only these five ASCII whitespace codepoints.
    source = value.strip(_SQLITE_WHITESPACE)
    if source.endswith(";"):
        source = source[:-1].rstrip(_SQLITE_WHITESPACE)

    tokens: list[str] = []
    punctuation = frozenset("(),;")
    operator_characters = frozenset(".=<>!|+-*/%&~:^?")
    index = 0
    while index < len(source):
        character = source[index]
        if character in _SQLITE_WHITESPACE:
            index += 1
            continue
        if character in {"'", '"', "`", "["}:
            start = index
            quote = character
            closing = "]" if quote == "[" else quote
            index += 1
            while index < len(source):
                current = source[index]
                index += 1
                if current != closing:
                    continue
                if quote != "[" and index < len(source) and source[index] == closing:
                    index += 1
                    continue
                break
            tokens.append(source[start:index])
            continue
        if character in punctuation:
            tokens.append(character)
            index += 1
            continue
        if character in operator_characters:
            start = index
            index += 1
            while (
                index < len(source)
                and source[index] in operator_characters
            ):
                index += 1
            tokens.append(source[start:index])
            continue

        start = index
        index += 1
        while index < len(source):
            current = source[index]
            if (
                current in _SQLITE_WHITESPACE
                or current in punctuation
                or current in operator_characters
                or current in {"'", '"', "`", "["}
            ):
                break
            index += 1
        tokens.append(source[start:index])

    return "".join(f"{len(token)}:{token}" for token in tokens)
