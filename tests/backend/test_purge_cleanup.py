from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from research_monitor.lifecycle import purge_project
from research_monitor.models import (
    AgentIntent,
    ArtifactRoot,
    IdempotencyRecord,
    OutboxEvent,
    PlanningProfile,
    Project,
    Proposal,
    SourceReference,
    TaskSourceReference,
)

from .conftest import enroll, mutate
from .test_api import op


def _seed_v02_project_records(
    client: TestClient,
    database,
    root: Path,
    *,
    name: str,
) -> dict:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "root_path": str(root)},
    )
    assert response.status_code == 201, response.text
    project = response.json()["project"]

    pipeline_id, task_id = str(uuid4()), str(uuid4())
    mutate(
        client,
        project,
        0,
        [
            op(
                "pipeline.create",
                {"id": pipeline_id, "title": "Purge cascade pipeline"},
            ),
            op(
                "task.create",
                {
                    "id": task_id,
                    "pipeline_id": pipeline_id,
                    "title": "Purge cascade task",
                },
            ),
        ],
    )

    intent_response = client.post(
        f"/api/v1/projects/{project['id']}/agent-prompts",
        json={
            "api_version": "1",
            "schema_version": "1",
            "mode": "reconcile_progress",
            "scope_type": "task",
            "scope_id": task_id,
            "instructions": "Check whether this monitor is already current.",
        },
    )
    assert intent_response.status_code == 201, intent_response.text
    intent = intent_response.json()
    proposal_response = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": intent["proposal_request_id"],
            "project_id": project["id"],
            "base_semantic_revision": 1,
            "proposal_contract_version": "2",
            "intent_id": intent["intent_id"],
            "result_kind": "no_changes",
            "no_change_reason": "up_to_date",
            "summary": "No monitor changes are required.",
            "actor_label": "Purge cascade test",
            "scan_summary": {
                "files_considered": 0,
                "files_read": 0,
                "text_bytes_read": 0,
                "truncated": False,
                "limitations": [],
            },
            "evidence": [
                {
                    "kind": "user_instruction",
                    "summary": "The bound request asked for a current-state check.",
                    "intent_id": intent["intent_id"],
                }
            ],
            "source_references": [],
            "operations": [],
        },
    )
    assert proposal_response.status_code == 201, proposal_response.text

    with database.write_session() as session:
        project_root = session.query(ArtifactRoot).filter_by(
            project_id=project["id"], is_project_root=True
        ).one()
        source = SourceReference(
            id=str(uuid4()),
            project_id=project["id"],
            task_id=task_id,
            source_root_id=project_root.id,
            source_path="PLAN.md",
            anchor="purge-cascade",
            opaque_key="PURGE-CASCADE",
            fingerprint="a" * 64,
        )
        session.add(source)
        session.flush()
        session.add(
            TaskSourceReference(
                id=str(uuid4()),
                project_id=project["id"],
                task_id=task_id,
                source_reference_id=source.id,
            )
        )
    return project


def test_permanent_purge_removes_project_scoped_coordination_rows(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    mutate(client, project, 0, [op("project.trash", {}, project["id"])])

    with database.session() as session:
        assert session.query(IdempotencyRecord).filter_by(project_id=project["id"]).count() > 0
        assert session.query(OutboxEvent).filter_by(project_id=project["id"]).count() > 0

    purge_project(database, project["id"], confirm=project["id"])

    with database.session() as session:
        assert session.query(IdempotencyRecord).filter_by(project_id=project["id"]).count() == 0
        assert session.query(OutboxEvent).filter_by(project_id=project["id"]).count() == 0


def test_permanent_purge_cascades_v02_records_without_touching_other_project(
    client: TestClient, project_root: Path, database
) -> None:
    other_root = project_root.parent / "other-project"
    other_root.mkdir()
    (other_root / "PLAN.md").write_text("# Other research plan\n", encoding="utf-8")

    project = _seed_v02_project_records(
        client,
        database,
        project_root,
        name="Purged project",
    )
    other = _seed_v02_project_records(
        client,
        database,
        other_root,
        name="Unrelated project",
    )

    scoped_models = (
        PlanningProfile,
        AgentIntent,
        Proposal,
        SourceReference,
        TaskSourceReference,
    )
    with database.session() as session:
        for model in scoped_models:
            assert session.query(model).filter_by(project_id=project["id"]).count() == 1
            assert session.query(model).filter_by(project_id=other["id"]).count() == 1

    mutate(client, project, 1, [op("project.trash", {}, project["id"])])
    purge_project(database, project["id"], confirm=project["id"])

    with database.session() as session:
        assert session.get(Project, project["id"]) is None
        assert session.get(Project, other["id"]) is not None
        for model in scoped_models:
            assert session.query(model).filter_by(project_id=project["id"]).count() == 0
            assert session.query(model).filter_by(project_id=other["id"]).count() == 1
