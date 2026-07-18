from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate


def _intent(
    client: TestClient,
    project_id: str,
    *,
    mode: str,
    scope_type: str = "project",
    scope_id: str | None = None,
    instructions: str = "",
    allow_completion: bool = False,
) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/agent-prompts",
        json={
            "api_version": "1",
            "schema_version": "1",
            "mode": mode,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "instructions": instructions,
            "allow_completion": allow_completion,
            "artifact_locators": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _instruction_evidence(intent_id: str) -> list[dict]:
    return [
        {
            "kind": "user_instruction",
            "intent_id": intent_id,
            "summary": "The bound dashboard request asks for this change.",
        }
    ]


def test_no_change_is_closed_idempotent_and_does_not_advance_revision(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    intent = _intent(
        client,
        project["id"],
        mode="initialize_structure",
        instructions="Inspect the empty project and report whether structure can be grounded.",
    )
    context = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": intent["intent_id"]},
    )
    assert context.status_code == 200, context.text
    assert context.json()["intent"]["bound_request_id"] == intent["proposal_request_id"]
    assert context.json()["journal_identity_index"]["items"] == []

    payload = {
        "api_version": "1",
        "schema_version": "1",
        "proposal_contract_version": "2",
        "request_id": intent["proposal_request_id"],
        "project_id": project["id"],
        "intent_id": intent["intent_id"],
        "base_semantic_revision": 0,
        "result_kind": "no_changes",
        "no_change_reason": "insufficient_evidence",
        "summary": "No grounded structure found",
        "rationale": "The scan found no source material.",
        "scan_summary": {
            "files_considered": 0,
            "files_read": 0,
            "text_bytes_read": 0,
            "truncated": False,
            "limitations": "No readable source files were present.",
        },
        "evidence": _instruction_evidence(intent["intent_id"]),
        "operations": [],
    }
    created = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=payload
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "no_changes"
    assert created.json()["result_kind"] == "no_changes"
    retried = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=payload
    )
    assert retried.status_code == 201
    assert retried.json()["id"] == created.json()["id"]
    snapshot = client.get(
        f"/api/v1/projects/{project['id']}/snapshot",
        params={"sections": "project,planning_profile"},
    ).json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["project"]["last_agent_check_at"] is not None
    cannot_apply = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [str(uuid4())],
        },
    )
    assert cannot_apply.status_code == 409
    assert cannot_apply.json()["detail"]["code"] == "proposal_closed"


def test_semantic_change_stales_intent_but_layout_change_does_not(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    first = _intent(client, project["id"], mode="initialize_structure")
    layout = client.post(
        f"/api/v1/projects/{project['id']}/layout-mutations",
        json={
            "project_id": project["id"],
            "base_layout_revision": 0,
            "operations": [
                {
                    "type": "viewport.upsert",
                    "data": {"x": 2, "y": 3, "zoom": 1},
                }
            ],
        },
    )
    assert layout.status_code == 200, layout.text
    still_valid = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": first["intent_id"]},
    )
    assert still_valid.status_code == 200, still_valid.text
    mutate(
        client,
        project,
        0,
        [
            {
                "type": "project.update",
                "entity_id": project["id"],
                "expected_version": 1,
                "data": {"description": "Changed manually"},
            }
        ],
    )
    stale = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": first["intent_id"]},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "intent_stale"


def test_initialize_derives_prerequisite_and_applies_atomically(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    intent = _intent(
        client,
        project["id"],
        mode="initialize_structure",
        instructions="Create a small source-grounded starting structure.",
    )
    pipeline_id = str(uuid4())
    task_id = str(uuid4())
    pipeline_operation_id = str(uuid4())
    task_operation_id = str(uuid4())
    evidence = _instruction_evidence(intent["intent_id"])
    payload = {
        "api_version": "1",
        "schema_version": "1",
        "proposal_contract_version": "2",
        "request_id": intent["proposal_request_id"],
        "project_id": project["id"],
        "intent_id": intent["intent_id"],
        "base_semantic_revision": 0,
        "result_kind": "changes",
        "summary": "Initial project structure",
        "rationale": "The user requested a minimal editable structure.",
        "scan_summary": {
            "files_considered": 0,
            "files_read": 0,
            "text_bytes_read": 0,
            "truncated": False,
            "limitations": "This is based on the bound user instruction.",
        },
        "operations": [
            {
                "id": pipeline_operation_id,
                "entity_id": pipeline_id,
                "type": "pipeline.create",
                "data": {"id": pipeline_id, "title": "Research plan"},
                "rationale": "Provide one editable planning container.",
                "confidence": 0.9,
                "basis": "user_instruction",
                "evidence": evidence,
            },
            {
                "id": task_operation_id,
                "entity_id": task_id,
                "type": "task.create",
                "data": {
                    "id": task_id,
                    "pipeline_id": pipeline_id,
                    "title": "Establish the first grounded milestone",
                    "status": "planned",
                    "outcome": "not_applicable",
                },
                "rationale": "Create one reviewable top-level task.",
                "confidence": 0.85,
                "basis": "user_instruction",
                "evidence": evidence,
            },
        ],
    }
    created = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=payload
    )
    assert created.status_code == 201, created.text
    operations = {item["id"]: item for item in created.json()["operations"]}
    assert pipeline_operation_id in operations[task_operation_id][
        "prerequisite_operation_ids"
    ]
    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [pipeline_operation_id, task_operation_id],
        },
    )
    assert applied.status_code == 200, applied.text
    snapshot = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()
    assert [item["title"] for item in snapshot["pipelines"]] == ["Research plan"]
    assert [item["title"] for item in snapshot["tasks"]] == [
        "Establish the first grounded milestone"
    ]
