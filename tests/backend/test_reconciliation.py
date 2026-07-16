from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def proposal_payload(project_id: str, request_id: str, pipeline_id: str, task_id: str, *, fresh_ids: bool = False) -> dict:
    pipeline_op = str(uuid4()); task_op = str(uuid4()); group_id = str(uuid4())
    return {
        "api_version": "1", "schema_version": "1", "request_id": request_id,
        "project_id": project_id, "base_semantic_revision": 0,
        "summary": "Source-anchored initial plan",
        "operations": [
            {"id": task_op, "type": "task.create", "atomic_group_id": group_id, "prerequisite_operation_ids": [pipeline_op], "data": {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}, "rationale": "Explicit task", "confidence": 0.9, "evidence": [{"kind": "source_text", "locator": "PLAN.md#task"}], "source_references": [{"path": "PLAN.md", "anchor": "task", "fingerprint": "sha256:task"}]},
            {"id": pipeline_op, "type": "pipeline.create", "atomic_group_id": group_id, "data": {"id": pipeline_id, "title": "Pipeline"}, "rationale": "Explicit pipeline", "confidence": 0.9, "evidence": [{"kind": "source_text", "locator": "PLAN.md#pipeline"}], "source_references": [{"path": "PLAN.md", "anchor": "pipeline", "fingerprint": "sha256:pipeline"}]},
        ],
    }


def test_exact_proposal_retry_precedes_stale_revision_check(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); request_id, pipeline_id, task_id = str(uuid4()), str(uuid4()), str(uuid4())
    payload = proposal_payload(project["id"], request_id, pipeline_id, task_id)
    first = client.post(f"/api/v1/projects/{project['id']}/proposals", json=payload)
    assert first.status_code == 201
    mutate(client, project, 0, [op("pipeline.create", {"title": "Manual work"})])
    retry = client.post(f"/api/v1/projects/{project['id']}/proposals", json=payload)
    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]


def test_semantically_identical_proposal_dedupes_fresh_internal_ids(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); pipeline_id, task_id = str(uuid4()), str(uuid4())
    first_payload = proposal_payload(project["id"], str(uuid4()), pipeline_id, task_id)
    second_payload = proposal_payload(project["id"], str(uuid4()), pipeline_id, task_id, fresh_ids=True)
    first = client.post(f"/api/v1/projects/{project['id']}/proposals", json=first_payload)
    second = client.post(f"/api/v1/projects/{project['id']}/proposals", json=second_payload)
    assert first.status_code == second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


def test_agent_proposal_rejects_destructive_and_unsupported_completion(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    destructive = {"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": 0, "summary": "Bad", "operations": [{"id": str(uuid4()), "type": "project.trash", "data": {}}]}
    assert client.post(f"/api/v1/projects/{project['id']}/proposals/validate", json=destructive).status_code == 403
    # Completion without a source/evidence item must be rejected, not accepted as a warning.
    missing_evidence = {"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": 0, "summary": "Unsupported", "operations": [{"id": str(uuid4()), "type": "task.update", "entity_id": str(uuid4()), "expected_version": 1, "data": {"status": "done", "completion_summary": "Claimed done"}}]}
    assert client.post(f"/api/v1/projects/{project['id']}/proposals/validate", json=missing_evidence).status_code in {403, 422}


def test_semantic_dedupe_alias_rejects_changed_retry_payload(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id, first_request, alias_request = [str(uuid4()) for _ in range(4)]
    first = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json=proposal_payload(project["id"], first_request, pipeline_id, task_id),
    )
    alias_payload = proposal_payload(project["id"], alias_request, pipeline_id, task_id)
    alias = client.post(f"/api/v1/projects/{project['id']}/proposals", json=alias_payload)
    assert first.status_code == alias.status_code == 201
    assert alias.json()["id"] == first.json()["id"]

    changed = proposal_payload(project["id"], alias_request, pipeline_id, task_id)
    changed["operations"][0]["data"]["title"] = "Changed retry"
    collision = client.post(f"/api/v1/projects/{project['id']}/proposals", json=changed)
    assert collision.status_code == 409
    assert collision.json()["detail"]["code"] == "idempotency_collision"


def test_proposal_warns_on_existing_active_task_title_without_source_identity(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, existing_task_id = str(uuid4()), str(uuid4())
    changed = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "Analysis"}),
        op("task.create", {"id": existing_task_id, "pipeline_id": pipeline_id, "title": "Evaluate baseline"}),
    ])
    operation_id = str(uuid4())
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json={
            "api_version": "1", "schema_version": "1", "request_id": str(uuid4()),
            "project_id": project["id"], "base_semantic_revision": changed["semantic_revision"],
            "summary": "Possible duplicate", "operations": [{
                "id": operation_id, "type": "task.create",
                "data": {"id": str(uuid4()), "pipeline_id": pipeline_id, "title": "Evaluate baseline"},
                "rationale": "A source heading uses this title", "confidence": 0.6,
                "evidence": [{"kind": "source_text", "locator": "PLAN.md#baseline"}],
            }],
        },
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    assert any(item["code"] == "possible_duplicate_existing_task" and item["operation_id"] == operation_id for item in warnings)
