from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from .conftest import enroll


def _pipeline_operation(
    *,
    operation_id: str | None = None,
    pipeline_id: str | None = None,
    title: str = "Original title",
    atomic_group_id: str | None = None,
    prerequisites: list[str] | None = None,
) -> dict:
    operation = {
        "id": operation_id or str(uuid4()),
        "type": "pipeline.create",
        "entity_id": pipeline_id or str(uuid4()),
        "data": {"title": title},
        "rationale": "PLAN.md defines this pipeline",
        "confidence": 0.9,
        "evidence": [{"path": "PLAN.md", "anchor": title}],
        "prerequisite_operation_ids": prerequisites or [],
    }
    if atomic_group_id is not None:
        operation["atomic_group_id"] = atomic_group_id
    return operation


def _create_proposal(client: TestClient, project_id: str, operations: list[dict]) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/proposals",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project_id,
            "base_semantic_revision": 0,
            "summary": "Create the documented research plan",
            "operations": operations,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _apply(
    client: TestClient,
    project_id: str,
    proposal_id: str,
    selected: list[str],
    overrides: list[dict],
):
    return client.post(
        f"/api/v1/projects/{project_id}/proposals/{proposal_id}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": selected,
            "operation_overrides": overrides,
        },
    )


def test_edited_operation_data_applies_and_is_persisted(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    operation = _pipeline_operation()
    proposal = _create_proposal(client, project["id"], [operation])

    edited = deepcopy(operation)
    edited["data"] = {"title": "Human-reviewed title", "description": "Edited before acceptance"}
    edited["rationale"] = "The reviewer clarified the intended workstream"
    edited["confidence"] = 1.0
    edited["evidence"] = [{"path": "PLAN.md", "anchor": "Reviewed plan"}]

    applied = _apply(
        client,
        project["id"],
        proposal["id"],
        [operation["id"]],
        [edited],
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["semantic_revision"] == 1

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert len(snapshot["pipelines"]) == 1
    assert snapshot["pipelines"][0]["id"] == operation["entity_id"]
    assert snapshot["pipelines"][0]["title"] == "Human-reviewed title"
    assert snapshot["pipelines"][0]["description"] == "Edited before acceptance"

    inspected = client.get(f"/api/v1/proposals/{proposal['id']}")
    assert inspected.status_code == 200
    assert inspected.json()["status"] == "applied"
    stored = inspected.json()["operations"][0]
    assert stored["data"] == edited["data"]
    assert stored["rationale"] == edited["rationale"]
    assert stored["confidence"] == edited["confidence"]
    assert stored["evidence"] == edited["evidence"]
    assert stored["disposition"] == "applied"


@pytest.mark.parametrize("immutable_field", ["type", "entity_id", "atomic_group_id", "prerequisites"])
def test_changing_operation_identity_rejects_without_partial_application(
    client: TestClient, project_root: Path, immutable_field: str
) -> None:
    project = enroll(client, project_root)
    group_id = str(uuid4()) if immutable_field == "atomic_group_id" else None
    operation = _pipeline_operation(atomic_group_id=group_id)
    proposal = _create_proposal(client, project["id"], [operation])
    edited = deepcopy(operation)
    edited["data"]["title"] = "Must not be applied"

    if immutable_field == "type":
        edited["type"] = "task.create"
    elif immutable_field == "entity_id":
        edited["entity_id"] = str(uuid4())
    elif immutable_field == "atomic_group_id":
        edited["atomic_group_id"] = str(uuid4())
    else:
        edited["prerequisite_operation_ids"] = [str(uuid4())]

    rejected = _apply(
        client,
        project["id"],
        proposal["id"],
        [operation["id"]],
        [edited],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "immutable_operation_identity"

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["pipelines"] == []
    inspected = client.get(f"/api/v1/proposals/{proposal['id']}").json()
    assert inspected["status"] == "draft"
    assert inspected["operations"][0]["data"]["title"] == "Original title"
    assert inspected["operations"][0]["disposition"] == "pending"


def test_override_for_unselected_operation_is_rejected_atomically(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    selected = _pipeline_operation(title="Selected")
    unselected = _pipeline_operation(title="Unselected")
    proposal = _create_proposal(client, project["id"], [selected, unselected])
    edited_unselected = deepcopy(unselected)
    edited_unselected["data"]["title"] = "Illegally edited"

    rejected = _apply(
        client,
        project["id"],
        proposal["id"],
        [selected["id"]],
        [edited_unselected],
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "invalid_operation_override"

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["pipelines"] == []
    inspected = client.get(f"/api/v1/proposals/{proposal['id']}").json()
    assert inspected["status"] == "draft"
    assert {operation["disposition"] for operation in inspected["operations"]} == {"pending"}



def test_override_cannot_change_data_encoded_entity_id(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    operation = _pipeline_operation()
    pipeline_id = operation.pop("entity_id")
    operation["data"]["id"] = pipeline_id
    proposal = _create_proposal(client, project["id"], [operation])

    edited = deepcopy(operation)
    edited["data"]["id"] = str(uuid4())
    rejected = _apply(client, project["id"], proposal["id"], [operation["id"]], [edited])

    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "immutable_operation_identity"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["pipelines"] == []


def test_operation_rejects_conflicting_entity_id_encodings(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    operation = _pipeline_operation()
    operation["data"]["id"] = str(uuid4())
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "api_version": "1", "schema_version": "1", "request_id": str(uuid4()),
            "project_id": project["id"], "base_semantic_revision": 0,
            "summary": "Conflicting target encodings", "operations": [operation],
        },
    )

    assert response.status_code == 422


def test_override_cannot_change_data_encoded_entity_id(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    operation = _pipeline_operation()
    pipeline_id = operation.pop("entity_id")
    operation["data"]["id"] = pipeline_id
    proposal = _create_proposal(client, project["id"], [operation])

    edited = deepcopy(operation)
    edited["data"]["id"] = str(uuid4())
    rejected = _apply(client, project["id"], proposal["id"], [operation["id"]], [edited])

    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["detail"]["code"] == "immutable_operation_identity"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["pipelines"] == []


def test_apply_request_id_rejects_changed_selection(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    first_operation = _pipeline_operation(title="First")
    second_operation = _pipeline_operation(title="Second")
    proposal = _create_proposal(client, project["id"], [first_operation, second_operation])
    request_id = str(uuid4())
    first = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal['id']}/apply",
        json={"request_id": request_id, "selected_operation_ids": [first_operation["id"]]},
    )
    collision = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal['id']}/apply",
        json={"request_id": request_id, "selected_operation_ids": [second_operation["id"]]},
    )

    assert first.status_code == 200
    assert collision.status_code == 409
    assert collision.json()["detail"]["code"] == "idempotency_collision"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert [pipeline["title"] for pipeline in snapshot["pipelines"]] == ["First"]


def test_apply_request_id_rejects_changed_override(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    operation = _pipeline_operation()
    proposal = _create_proposal(client, project["id"], [operation])
    request_id = str(uuid4())
    reviewed = deepcopy(operation)
    reviewed["data"]["title"] = "Reviewed"
    first = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal['id']}/apply",
        json={"request_id": request_id, "selected_operation_ids": [operation["id"]], "operation_overrides": [reviewed]},
    )
    changed = deepcopy(reviewed)
    changed["data"]["title"] = "Changed retry"
    collision = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal['id']}/apply",
        json={"request_id": request_id, "selected_operation_ids": [operation["id"]], "operation_overrides": [changed]},
    )

    assert first.status_code == 200
    assert collision.status_code == 409
    assert collision.json()["detail"]["code"] == "idempotency_collision"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["pipelines"][0]["title"] == "Reviewed"
