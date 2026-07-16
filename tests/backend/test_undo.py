from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll
from .test_api import op


def mutation(
    client: TestClient,
    project_id: str,
    revision: int,
    operations: list[dict],
    *,
    request_id: str | None = None,
) -> tuple[str, dict]:
    request_id = request_id or str(uuid4())
    response = client.post(
        f"/api/v1/projects/{project_id}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": request_id,
            "project_id": project_id,
            "base_semantic_revision": revision,
            "actor_type": "ui",
            "actor_label": "undo test",
            "operations": operations,
        },
    )
    assert response.status_code == 200, response.text
    return request_id, response.json()


def test_undo_metadata_edit_after_unrelated_change_is_atomic_and_idempotent(
    client: TestClient,
    project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, unrelated_id = str(uuid4()), str(uuid4())
    _, created = mutation(
        client,
        project["id"],
        0,
        [op("pipeline.create", {"id": pipeline_id, "title": "Original"})],
    )
    target_request_id, edited = mutation(
        client,
        project["id"],
        created["semantic_revision"],
        [op("pipeline.update", {"title": "Edited"}, pipeline_id, 1)],
    )
    _, unrelated = mutation(
        client,
        project["id"],
        edited["semantic_revision"],
        [op("pipeline.create", {"id": unrelated_id, "title": "Unrelated"})],
    )

    history = client.get(f"/api/v1/projects/{project['id']}/history").json()["events"]
    target_events = [event for event in history if event["request_id"] == target_request_id]
    assert len(target_events) == 1
    assert target_events[0]["undoable"] is True
    assert target_events[0]["undo_request_head"] is True

    undo_request_id = str(uuid4())
    body = {
        "request_id": undo_request_id,
        "base_semantic_revision": unrelated["semantic_revision"],
    }
    first = client.post(
        f"/api/v1/projects/{project['id']}/mutations/{target_request_id}/undo",
        json=body,
    )
    second = client.post(
        f"/api/v1/projects/{project['id']}/mutations/{target_request_id}/undo",
        json=body,
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["undone_request_id"] == target_request_id
    assert first.json()["semantic_revision"] == unrelated["semantic_revision"] + 1

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    pipeline = next(item for item in snapshot["pipelines"] if item["id"] == pipeline_id)
    assert pipeline["title"] == "Original"
    assert any(item["id"] == unrelated_id for item in snapshot["pipelines"])

    refreshed = client.get(f"/api/v1/projects/{project['id']}/history").json()["events"]
    original = next(event for event in refreshed if event["request_id"] == target_request_id)
    inverse = next(event for event in refreshed if event["request_id"] == undo_request_id)
    assert original["undoable"] is False
    assert original["undo_code"] == "already_undone"
    assert inverse["actor_label"].startswith("Undo of ")
    assert inverse["undoable"] is False


def test_undo_create_refuses_later_transitive_dependents(
    client: TestClient,
    project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    target_request_id, created = mutation(
        client,
        project["id"],
        0,
        [op("pipeline.create", {"id": pipeline_id, "title": "Work"})],
    )
    _, changed = mutation(
        client,
        project["id"],
        created["semantic_revision"],
        [op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Later task"})],
    )

    event = next(
        item
        for item in client.get(f"/api/v1/projects/{project['id']}/history").json()["events"]
        if item["request_id"] == target_request_id
    )
    assert event["undoable"] is False
    assert event["undo_code"] == "undo_conflict"

    response = client.post(
        f"/api/v1/projects/{project['id']}/mutations/{target_request_id}/undo",
        json={"request_id": str(uuid4()), "base_semantic_revision": changed["semantic_revision"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "undo_conflict"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert any(item["id"] == pipeline_id and item["deleted_at"] is None for item in snapshot["pipelines"])
    assert any(item["id"] == task_id and item["deleted_at"] is None for item in snapshot["tasks"])


def test_completion_transition_is_explicitly_non_undoable(
    client: TestClient,
    project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    _, created = mutation(
        client,
        project["id"],
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Experiments"}),
            op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Baseline"}),
        ],
    )
    target_request_id, completed = mutation(
        client,
        project["id"],
        created["semantic_revision"],
        [
            op(
                "task.update",
                {"status": "done", "completion_summary": "Finished with evidence"},
                task_id,
                1,
            )
        ],
    )
    event = next(
        item
        for item in client.get(f"/api/v1/projects/{project['id']}/history").json()["events"]
        if item["request_id"] == target_request_id
    )
    assert event["undoable"] is False
    assert "Completion" in event["undo_reason"]

    response = client.post(
        f"/api/v1/projects/{project['id']}/mutations/{target_request_id}/undo",
        json={"request_id": str(uuid4()), "base_semantic_revision": completed["semantic_revision"]},
    )
    assert response.status_code == 409
    task = next(
        item
        for item in client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["tasks"]
        if item["id"] == task_id
    )
    assert task["status"] == "done"
    assert task["completion_summary"] == "Finished with evidence"
