from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from research_monitor.models import Project, ProposalOperation
from research_monitor.schemas import Operation, ProposalEnvelope

from .conftest import enroll, mutate
from .test_api import op


def _agent_operation(
    operation_type: str,
    data: dict,
    *,
    entity_id: str | None = None,
    expected_version: int | None = None,
) -> dict:
    value = {
        "id": str(uuid4()),
        "type": operation_type,
        "data": data,
        "rationale": "PLAN.md records this change",
        "confidence": 0.9,
        "evidence": [{"kind": "document", "locator": "PLAN.md"}],
    }
    if entity_id is not None:
        value["entity_id"] = entity_id
    if expected_version is not None:
        value["expected_version"] = expected_version
    return value


def _create_proposal(
    client: TestClient,
    project_id: str,
    base_revision: int,
    operations: list[dict],
) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/proposals",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project_id,
            "base_semantic_revision": base_revision,
            "summary": "Reconcile the documented plan",
            "operations": operations,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_proposal_persists_canonical_create_update_and_link_diffs_across_staleness(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id, artifact_id = [str(uuid4()) for _ in range(3)]
    mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Original"}),
            op(
                "task.create",
                {"id": task_id, "pipeline_id": pipeline_id, "title": "Run analysis"},
            ),
            op(
                "artifact.create",
                {
                    "id": artifact_id,
                    "kind": "url",
                    "locator": "https://wandb.ai/example/run",
                    "label": "Run",
                },
            ),
        ],
    )

    created_pipeline_id, link_id = str(uuid4()), str(uuid4())
    update = _agent_operation(
        "pipeline.update",
        {"title": "Proposed"},
        entity_id=pipeline_id,
        expected_version=1,
    )
    create = _agent_operation(
        "pipeline.create",
        {"title": "Created"},
        entity_id=created_pipeline_id,
    )
    link = _agent_operation(
        "task_artifact.link",
        {"task_id": task_id, "artifact_id": artifact_id, "role": "evidence"},
        entity_id=link_id,
    )
    proposal = _create_proposal(client, project["id"], 1, [update, create, link])
    operations = {item["id"]: item for item in proposal["operations"]}

    assert operations[update["id"]]["before"]["title"] == "Original"
    assert operations[update["id"]]["before"]["version"] == 1
    assert operations[update["id"]]["after"]["title"] == "Proposed"
    assert operations[update["id"]]["after"]["version"] == 2
    assert operations[create["id"]]["before"] is None
    assert operations[create["id"]]["after"]["id"] == created_pipeline_id
    assert operations[create["id"]]["after"]["flow_mode"] == "sequential"
    assert operations[create["id"]]["after"]["version"] == 1
    assert operations[link["id"]]["before"] is None
    assert operations[link["id"]]["after"] == {
        "id": link_id,
        "project_id": project["id"],
        "task_id": task_id,
        "artifact_id": artifact_id,
        "role": "evidence",
        "notes": "",
        "created_at": operations[link["id"]]["after"]["created_at"],
    }

    with database.session() as session:
        rows = session.scalars(
            select(ProposalOperation).where(
                ProposalOperation.proposal_id == proposal["id"]
            )
        ).all()
        for row in rows:
            stored = json.loads(row.operation_json)
            assert set(stored) == {"operation", "diff"}
            assert stored["diff"]["before"] == operations[row.id]["before"]
            assert stored["diff"]["after"] == operations[row.id]["after"]

    original_diffs = {
        operation_id: (deepcopy(value["before"]), deepcopy(value["after"]))
        for operation_id, value in operations.items()
    }
    mutate(
        client,
        project,
        1,
        [op("pipeline.update", {"title": "Manual later"}, pipeline_id, 1)],
    )
    conflict = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [update["id"], create["id"], link["id"]],
        },
    )
    assert conflict.status_code == 409
    inspected = client.get(f"/api/v1/proposals/{proposal['id']}").json()
    assert inspected["status"] == "conflict"
    for value in inspected["operations"]:
        assert (value["before"], value["after"]) == original_diffs[value["id"]]


def test_apply_override_preserves_before_and_replaces_after_with_canonical_result(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id = str(uuid4())
    mutate(client, project, 0, [op("pipeline.create", {"id": pipeline_id, "title": "Original"})])
    operation = _agent_operation(
        "pipeline.update",
        {"title": "Agent"},
        entity_id=pipeline_id,
        expected_version=1,
    )
    proposal = _create_proposal(client, project["id"], 1, [operation])
    before = deepcopy(proposal["operations"][0]["before"])
    override = deepcopy(operation)
    override["data"] = {"title": "Reviewed", "description": "Human clarification"}
    override["rationale"] = "The reviewer clarified the scope"

    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [operation["id"]],
            "operation_overrides": [override],
        },
    )
    assert applied.status_code == 200, applied.text
    stored = client.get(f"/api/v1/proposals/{proposal['id']}").json()["operations"][0]
    assert stored["before"] == before
    assert stored["after"]["title"] == "Reviewed"
    assert stored["after"]["description"] == "Human clarification"
    assert stored["after"]["version"] == 2
    assert stored["data"] == override["data"]


def test_legacy_bare_operation_remains_readable_and_override_upgrades_it(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    pipeline_id = str(uuid4())
    mutate(client, project, 0, [op("pipeline.create", {"id": pipeline_id, "title": "Original"})])
    operation = _agent_operation(
        "pipeline.update",
        {"title": "Agent"},
        entity_id=pipeline_id,
        expected_version=1,
    )
    proposal = _create_proposal(client, project["id"], 1, [operation])
    with database.write_session() as session:
        row = session.get(ProposalOperation, operation["id"])
        assert row is not None
        row.operation_json = json.dumps(json.loads(row.operation_json)["operation"])

    legacy = client.get(f"/api/v1/proposals/{proposal['id']}").json()["operations"][0]
    assert "before" not in legacy
    assert "after" not in legacy
    override = deepcopy(operation)
    override["data"] = {"title": "Reviewed legacy"}
    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [operation["id"]],
            "operation_overrides": [override],
        },
    )
    assert applied.status_code == 200, applied.text
    upgraded = client.get(f"/api/v1/proposals/{proposal['id']}").json()["operations"][0]
    assert upgraded["before"]["title"] == "Original"
    assert upgraded["after"]["title"] == "Reviewed legacy"


def test_shared_diff_capture_represents_soft_delete_without_persisting_dry_run(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Plan"}),
            op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}),
        ],
    )
    delete = Operation(
        id=uuid4(),
        type="task.delete",
        entity_id=UUID(task_id),
        expected_version=1,
        data={},
    )
    envelope = ProposalEnvelope(
        request_id=uuid4(),
        project_id=UUID(project["id"]),
        base_semantic_revision=1,
        summary="Exercise canonical delete capture",
        operations=[delete],
    )
    service = client.app.state.service
    with database.write_session() as session:
        project_row = session.get(Project, project["id"])
        assert project_row is not None
        diff = service._dry_run_operation_diffs(
            session, project_row, envelope, [delete]
        )[str(delete.id)]
    assert diff["before"]["deleted_at"] is None
    assert diff["after"]["deleted_at"] is not None
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 1
    assert {task["id"] for task in snapshot["tasks"]} == {task_id}
