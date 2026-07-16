from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def _source_operation(operation_type: str, data: dict, *, operation_id: str | None = None, prerequisites: list[str] | None = None, source_anchor: str) -> dict:
    return {
        "id": operation_id or str(uuid4()), "type": operation_type,
        "prerequisite_operation_ids": prerequisites or [], "data": data,
        "rationale": f"The source explicitly describes {source_anchor}", "confidence": 0.85,
        "evidence": [{"kind": "source_text", "summary": f"Evidence for {source_anchor}", "locator": f"PLAN.md#{source_anchor}"}],
        "source_references": [{"path": "PLAN.md", "anchor": source_anchor, "opaque_key": source_anchor.upper(), "fingerprint": f"sha256:{source_anchor}"}],
    }


def _create_initial_proposal(client: TestClient, project_id: str) -> tuple[dict, dict[str, str], list[dict]]:
    pipeline_id, parent_task_id = str(uuid4()), str(uuid4())
    pipeline_operation_id, task_operation_id = str(uuid4()), str(uuid4())
    operations = [
        _source_operation("pipeline.create", {"id": pipeline_id, "title": "Experiment pipeline", "position": 0}, operation_id=pipeline_operation_id, source_anchor="pipeline"),
        _source_operation("task.create", {"id": parent_task_id, "pipeline_id": pipeline_id, "title": "Run experiments", "position": 0}, operation_id=task_operation_id, prerequisites=[pipeline_operation_id], source_anchor="experiments"),
    ]
    response = client.post(f"/api/v1/projects/{project_id}/proposals", json={
        "api_version": "1", "schema_version": "1", "request_id": str(uuid4()),
        "project_id": project_id, "base_semantic_revision": 0,
        "summary": "Initial flat draft", "rationale": "Initial source scan",
        "actor_label": "Codex", "operations": operations,
    })
    assert response.status_code == 201, response.text
    return response.json(), {
        "pipeline_id": pipeline_id, "parent_task_id": parent_task_id,
        "pipeline_operation_id": pipeline_operation_id, "task_operation_id": task_operation_id,
    }, operations


def _revision_payload(project_id: str, ids: dict[str, str], original_operations: list[dict], *, request_id: str | None = None) -> dict:
    parent = deepcopy(original_operations[1])
    parent["data"].update({"title": "Experiment program", "description": "Parent grouping task created during staging.", "child_flow_mode": "sequential"})
    parent["rationale"] = "A human editor split the broad experiment task"
    child_one_operation_id, child_two_operation_id = str(uuid4()), str(uuid4())
    child_one = _source_operation("task.create", {
        "id": str(uuid4()), "pipeline_id": ids["pipeline_id"], "parent_id": ids["parent_task_id"],
        "title": "Run baseline", "position": 0,
    }, operation_id=child_one_operation_id, prerequisites=[ids["task_operation_id"]], source_anchor="baseline")
    child_two = _source_operation("task.create", {
        "id": str(uuid4()), "pipeline_id": ids["pipeline_id"], "parent_id": ids["parent_task_id"],
        "title": "Run ablation", "position": 1,
    }, operation_id=child_two_operation_id, prerequisites=[ids["task_operation_id"], child_one_operation_id], source_anchor="ablation")
    return {
        "api_version": "1", "schema_version": "1", "request_id": request_id or str(uuid4()),
        "project_id": project_id, "base_semantic_revision": 0, "actor_type": "ui",
        "actor_label": "Human staging editor", "summary": "Hierarchical experiment draft",
        "rationale": "Split the broad task into reviewable sequential leaves.",
        "operations": [deepcopy(original_operations[0]), parent, child_one, child_two],
    }


def test_human_revision_supersedes_original_with_full_validated_draft(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    original, ids, source_operations = _create_initial_proposal(client, project["id"])
    original_operation_content = deepcopy(original["operations"])
    payload = _revision_payload(project["id"], ids, source_operations)
    response = client.post(f"/api/v1/projects/{project["id"]}/proposals/{original["id"]}/revisions", json=payload)

    assert response.status_code == 201, response.text
    replacement = response.json()
    assert replacement["id"] == payload["request_id"]
    assert replacement["status"] == "draft"
    assert replacement["actor_label"] == "Human staging editor"
    assert replacement["supersedes_proposal_id"] == original["id"]
    assert replacement["superseded_by_proposal_id"] is None
    assert len(replacement["operations"]) == 4
    assert all("before" in item and "after" in item for item in replacement["operations"])
    replacement_operation_ids = {item["id"] for item in replacement["operations"]}
    assert replacement_operation_ids.isdisjoint({item["id"] for item in source_operations})
    for item in replacement["operations"]:
        assert set(item["prerequisite_operation_ids"]).issubset(replacement_operation_ids)

    inspected_original = client.get(f"/api/v1/proposals/{original["id"]}").json()
    assert inspected_original["status"] == "superseded"
    assert inspected_original["closed_at"] is not None
    assert inspected_original["superseded_by_proposal_id"] == replacement["id"]
    assert inspected_original["supersedes_proposal_id"] is None
    assert inspected_original["operations"] == original_operation_content

    snapshot = client.get(f"/api/v1/projects/{project["id"]}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["pipelines"] == []
    assert snapshot["tasks"] == []


def test_revision_retry_is_idempotent_and_changed_retry_collides(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    original, ids, source_operations = _create_initial_proposal(client, project["id"])
    payload = _revision_payload(project["id"], ids, source_operations, request_id=str(uuid4()))
    route = f"/api/v1/projects/{project["id"]}/proposals/{original["id"]}/revisions"
    first = client.post(route, json=payload)
    retry = client.post(route, json=payload)
    assert first.status_code == retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    changed = deepcopy(payload)
    changed["summary"] = "Changed retry payload"
    collision = client.post(route, json=changed)
    assert collision.status_code == 409
    assert collision.json()["detail"]["code"] == "idempotency_collision"
    assert len(client.get(f"/api/v1/projects/{project["id"]}/proposals").json()["proposals"]) == 2


def test_revision_conflict_and_invalid_dependency_leave_original_open(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    original, ids, source_operations = _create_initial_proposal(client, project["id"])
    route = f"/api/v1/projects/{project["id"]}/proposals/{original["id"]}/revisions"
    invalid = _revision_payload(project["id"], ids, source_operations)
    invalid["operations"][-1]["prerequisite_operation_ids"].append(str(uuid4()))
    invalid_response = client.post(route, json=invalid)
    assert invalid_response.status_code == 422
    assert invalid_response.json()["detail"]["code"] == "missing_operation_prerequisite"
    assert client.get(f"/api/v1/proposals/{original["id"]}").json()["status"] == "draft"
    dry_run_invalid = _revision_payload(project["id"], ids, source_operations)
    dry_run_invalid["operations"][-1]["data"]["pipeline_id"] = str(uuid4())
    dry_run_response = client.post(route, json=dry_run_invalid)
    assert dry_run_response.status_code == 422
    assert dry_run_response.json()["detail"]["code"] == "invalid_pipeline"
    assert client.get(f"/api/v1/proposals/{original["id"]}").json()["status"] == "draft"

    base_mismatch = _revision_payload(project["id"], ids, source_operations)
    base_mismatch["base_semantic_revision"] = 1
    mismatch_response = client.post(route, json=base_mismatch)
    assert mismatch_response.status_code == 409
    assert mismatch_response.json()["detail"]["code"] == "proposal_revision_base_mismatch"
    assert client.get(f"/api/v1/proposals/{original["id"]}").json()["status"] == "draft"

    mutate(client, project, 0, [op("pipeline.create", {"title": "Manual work"})])
    stale = _revision_payload(project["id"], ids, source_operations)
    stale_response = client.post(route, json=stale)
    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["code"] == "revision_conflict"
    assert client.get(f"/api/v1/proposals/{original["id"]}").json()["status"] == "draft"
    assert len(client.get(f"/api/v1/projects/{project["id"]}/proposals").json()["proposals"]) == 1


def test_revision_rejects_descendant_edit_after_cross_pipeline_subtree_move(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    source_pipeline_id, destination_pipeline_id, parent_id, child_id = [str(uuid4()) for _ in range(4)]
    setup = mutate(client, project, 0, [
        op("pipeline.create", {"id": source_pipeline_id, "title": "Source"}),
        op("pipeline.create", {"id": destination_pipeline_id, "title": "Destination"}),
        op("task.create", {"id": parent_id, "pipeline_id": source_pipeline_id, "title": "Parent"}),
        op("task.create", {"id": child_id, "pipeline_id": source_pipeline_id, "parent_id": parent_id, "title": "Child"}),
    ])
    move_operation_id = str(uuid4())
    move_parent = _source_operation(
        "task.update",
        {"pipeline_id": destination_pipeline_id, "parent_id": None},
        operation_id=move_operation_id,
        source_anchor="move-parent",
    )
    move_parent.update({"entity_id": parent_id, "expected_version": 1})
    created = client.post(f"/api/v1/projects/{project['id']}/proposals", json={
        "api_version": "1", "schema_version": "1", "request_id": str(uuid4()),
        "project_id": project["id"], "base_semantic_revision": setup["semantic_revision"],
        "summary": "Move the task subtree", "rationale": "The workflow changed pipelines.",
        "actor_label": "Codex", "operations": [move_parent],
    })
    assert created.status_code == 201, created.text
    original = created.json()
    edit_child = _source_operation(
        "task.update",
        {"priority": "optional"},
        prerequisites=[move_operation_id],
        source_anchor="edit-child",
    )
    edit_child.update({"entity_id": child_id, "expected_version": 1})

    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{original['id']}/revisions",
        json={
            "api_version": "1", "schema_version": "1", "request_id": str(uuid4()),
            "project_id": project["id"], "base_semantic_revision": setup["semantic_revision"],
            "actor_type": "ui", "actor_label": "Human staging editor",
            "summary": "Unsafe combined subtree revision",
            "rationale": "This is the combination the graphical guard must prevent.",
            "operations": [move_parent, edit_child],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "entity_version_conflict"
    assert client.get(f"/api/v1/proposals/{original['id']}").json()["status"] == "draft"


def test_agent_context_exposes_only_compact_open_draft_reconciliation_data(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    original, _ids, _source_operations = _create_initial_proposal(client, project["id"])
    response = client.get(f"/api/v1/projects/{project["id"]}/agent-context")
    assert response.status_code == 200
    drafts = response.json()["open_proposal_drafts"]
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["id"] == original["id"]
    assert draft["summary"] == "Initial flat draft"
    assert draft["operation_count"] == 2
    assert draft["operation_type_counts"] == {"pipeline.create": 1, "task.create": 1}
    assert draft["source_identity_count"] == 2
    assert {item["path"] for item in draft["source_identities"]} == {"PLAN.md"}
    assert {item["fingerprint"] for item in draft["source_identities"]} == {"sha256:pipeline", "sha256:experiments"}
    assert set(draft) == {
        "id", "base_semantic_revision", "summary", "operation_count", "operation_type_counts",
        "source_identity_count", "source_identities", "created_at",
    }
    serialized = json.dumps(draft)
    assert "Initial source scan" not in serialized
    assert "Run experiments" not in serialized
    assert "Evidence for experiments" not in serialized


def test_revision_endpoint_is_ui_only(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    original, ids, source_operations = _create_initial_proposal(client, project["id"])
    payload = _revision_payload(project["id"], ids, source_operations)
    payload["actor_type"] = "agent"
    response = client.post(f"/api/v1/projects/{project["id"]}/proposals/{original["id"]}/revisions", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
