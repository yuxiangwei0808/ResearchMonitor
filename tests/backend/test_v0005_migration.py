from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text

from research_monitor.database import Database
from research_monitor.migrations.schema_v0005 import (
    PLANNING_PROFILES_TABLE,
    validate_v0005_schema,
)
from research_monitor.models import Base
from research_monitor.schema_validation import validate_current_schema


def _legacy_rows(database: Database, tmp_path: Path) -> dict[str, str]:
    values = {
        name: str(uuid4())
        for name in ("project", "root", "pipeline", "task", "journal", "source", "proposal", "operation")
    }
    project_root = tmp_path / "project"
    project_root.mkdir(exist_ok=True)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO projects "
                "(id,name,root_path,description,research_goal,success_criteria,color,"
                "semantic_revision,layout_revision,entity_version,created_at,updated_at) "
                "VALUES (:id,'Legacy',:root,'','','','#123456',0,0,1,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"id": values["project"], "root": str(project_root)},
        )
        connection.execute(
            text(
                "INSERT INTO scan_policies "
                "(project_id,preferred_sources_json,include_globs_json,exclude_globs_json,"
                "sensitive_patterns_json,max_text_bytes,allow_git_metadata,git_history_limit,"
                "allow_outside_sources,follow_symlinks,entity_version) "
                "VALUES (:id,'[]','[]','[]','[]',100,1,10,1,0,1)"
            ),
            {"id": values["project"]},
        )
        connection.execute(
            text(
                "INSERT INTO artifact_roots "
                "(id,project_id,alias,root_path,is_project_root,entity_version,created_at) "
                "VALUES (:id,:project,'Project root',:root,1,1,CURRENT_TIMESTAMP)"
            ),
            {
                "id": values["root"],
                "project": values["project"],
                "root": str(project_root),
            },
        )
        connection.execute(
            text(
                "INSERT INTO pipelines "
                "(id,project_id,title,description,flow_mode,order_index,entity_version,"
                "created_at,updated_at) VALUES "
                "(:id,:project,'Pipeline','','freeform',0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"id": values["pipeline"], "project": values["project"]},
        )
        connection.execute(
            text(
                "INSERT INTO tasks "
                "(id,project_id,pipeline_id,title,description,kind,status,outcome,priority,"
                "labels_json,order_index,completion_criteria,blocker_reason,completion_summary,"
                "completion_actor,completion_source,completion_override_reason,"
                "completion_provenance,child_flow_mode,entity_version,created_at,updated_at) "
                "VALUES (:id,:project,:pipeline,'Task','','task','planned','not_applicable',"
                "'required','[]',0,'','','','','','','','freeform',1,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {
                "id": values["task"],
                "project": values["project"],
                "pipeline": values["pipeline"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO journal_entries "
                "(id,project_id,task_id,entry_type,content,occurred_at,entity_version,"
                "created_at,updated_at) VALUES "
                "(:id,:project,:task,'note','legacy body',CURRENT_TIMESTAMP,1,"
                "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {
                "id": values["journal"],
                "project": values["project"],
                "task": values["task"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO source_references "
                "(id,project_id,task_id,source_path,anchor,opaque_key,fingerprint,imported_at) "
                "VALUES (:id,:project,:task,'README.md','tasks','LEG-1','abc',CURRENT_TIMESTAMP)"
            ),
            {
                "id": values["source"],
                "project": values["project"],
                "task": values["task"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO proposals "
                "(id,project_id,request_id,base_semantic_revision,summary,rationale,status,"
                "fingerprint,actor_label,rejection_reason,created_at) VALUES "
                "(:id,:project,:request,0,'Legacy proposal','','pending','fp','Codex','',"
                "CURRENT_TIMESTAMP)"
            ),
            {
                "id": values["proposal"],
                "project": values["project"],
                "request": str(uuid4()),
            },
        )
        connection.execute(
            text(
                "INSERT INTO proposal_operations "
                "(id,proposal_id,operation_type,operation_json,prerequisites_json,rationale,"
                "evidence_json,source_references_json,disposition) VALUES "
                "(:id,:proposal,'task.update','{}','[]','','[]','[]','pending')"
            ),
            {"id": values["operation"], "proposal": values["proposal"]},
        )
    return values


def test_upgrade_0004_backfills_guided_storage_without_changing_compat_version(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "legacy.db")
    command.upgrade(database._alembic_config(), "0004")
    values = _legacy_rows(database, tmp_path)

    command.upgrade(database._alembic_config(), "0005")

    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert connection.scalar(text("SELECT max(version) FROM schema_versions")) == 1
        profile = connection.execute(
            text(
                "SELECT task_granularity,max_nesting_depth,planning_horizon,"
                "inference_policy,max_new_tasks_per_proposal FROM planning_profiles "
                "WHERE project_id=:project"
            ),
            {"project": values["project"]},
        ).one()
        assert profile == ("balanced", 3, "current_milestone", "cautious_gaps", 30)
        policy = connection.execute(
            text(
                "SELECT readable_source_root_ids_json,max_files_per_scan,"
                "max_total_text_bytes,allow_outside_sources FROM scan_policies"
            )
        ).one()
        assert policy == ("[]", 500, 10 * 1024 * 1024, 0)
        assert connection.scalar(
            text("SELECT source_root_id FROM source_references WHERE id=:id"),
            {"id": values["source"]},
        ) == values["root"]
        assert connection.scalar(
            text("SELECT count(*) FROM task_source_references WHERE task_id=:task"),
            {"task": values["task"]},
        ) == 1
        assert connection.scalar(
            text("SELECT content_sha256 FROM journal_entries WHERE id=:id"),
            {"id": values["journal"]},
        ) == hashlib.sha256(b"legacy body").hexdigest()
        assert connection.execute(
            text(
                "SELECT proposal_contract_version,workflow_mode,scope_type,result_kind,"
                "fingerprint_version FROM proposals WHERE id=:id"
            ),
            {"id": values["proposal"]},
        ).one() == ("1", "legacy_custom", "project", "changes", 1)
        assert connection.scalar(
            text("SELECT basis FROM proposal_operations WHERE id=:id"),
            {"id": values["operation"]},
        ) == ""
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        validate_v0005_schema(inspect(connection), require_complete=True)
        validate_current_schema(connection)
    database.engine.dispose()


def test_revision_0005_resumes_safe_partial_objects(tmp_path: Path) -> None:
    database = Database(tmp_path / "partial.db")
    command.upgrade(database._alembic_config(), "0004")
    values = _legacy_rows(database, tmp_path)
    with database.engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE projects ADD COLUMN last_agent_check_at DATETIME")
        )
        PLANNING_PROFILES_TABLE.create(bind=connection)
        connection.execute(
            PLANNING_PROFILES_TABLE.insert().values(
                project_id=values["project"],
                task_granularity="coarse",
                max_nesting_depth=2,
                planning_horizon="immediate",
                inference_policy="sources_only",
                max_new_tasks_per_proposal=4,
                preferred_pipeline_names_json="[]",
                terminology_notes="preserve",
                additional_instructions="",
                protected_pipeline_ids_json="[]",
                protected_task_ids_json="[]",
                entity_version=7,
                created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        )

    command.upgrade(database._alembic_config(), "0005")

    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        assert connection.execute(
            text(
                "SELECT task_granularity,terminology_notes,entity_version "
                "FROM planning_profiles WHERE project_id=:project"
            ),
            {"project": values["project"]},
        ).one() == ("coarse", "preserve", 7)
        assert connection.execute(text("PRAGMA foreign_key_check")).fetchall() == []
        validate_current_schema(connection)
    database.engine.dispose()


def test_revision_0005_rejects_malformed_partial_column_before_writes(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "malformed.db")
    command.upgrade(database._alembic_config(), "0004")
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE projects ADD COLUMN last_agent_check_at "
                "TEXT NOT NULL DEFAULT ''"
            )
        )

    with pytest.raises(RuntimeError, match="last_agent_check_at.*nullability|last_agent_check_at"):
        command.upgrade(database._alembic_config(), "0005")

    with database.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
        assert "planning_profiles" not in inspect(connection).get_table_names()
    database.engine.dispose()


def test_complete_create_all_is_adopted_but_incomplete_future_shape_is_not(
    tmp_path: Path,
) -> None:
    complete = Database(tmp_path / "complete.db")
    Base.metadata.create_all(complete.engine)
    complete.initialize()
    with complete.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
        validate_current_schema(connection)
    complete.engine.dispose()

    incomplete = Database(tmp_path / "incomplete.db")
    Base.metadata.create_all(incomplete.engine)
    with incomplete.engine.begin() as connection:
        connection.execute(text("DROP TABLE planning_profiles"))
    with pytest.raises(RuntimeError, match="revision-0005 tables|revision 0005"):
        incomplete.initialize()
    with incomplete.engine.connect() as connection:
        assert "alembic_version" not in inspect(connection).get_table_names()
    incomplete.engine.dispose()
