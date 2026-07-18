from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def _task(client: TestClient, project_id: str, task_id: str) -> dict:
    snapshot = client.get(f"/api/v1/projects/{project_id}/snapshot").json()
    return next(item for item in snapshot["tasks"] if item["id"] == task_id)


def test_completion_provenance_is_transition_aware(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id, artifact_id = str(uuid4()), str(uuid4()), str(uuid4())
    created = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Experiments"}),
            op("task.create", {
                "id": task_id,
                "pipeline_id": pipeline_id,
                "title": "Run planned comparison",
            }),
            op("artifact.create", {
                "id": artifact_id,
                "kind": "url",
                "locator": "https://example.test/results/final.json",
                "label": "Final result",
            }),
        ],
    )
    planned = _task(client, project["id"], task_id)
    assert planned["completed_at"] is None
    assert planned["completion_actor"] is None
    assert planned["completion_source"] is None
    assert planned["completion_provenance"] is None

    operation_id = str(uuid4())
    proposed = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": created["semantic_revision"],
            "actor_label": "Codex reconciliation",
            "summary": "Record the documented result",
            "operations": [{
                "id": operation_id,
                "type": "task.update",
                "entity_id": task_id,
                "expected_version": planned["version"],
                "data": {
                    "status": "done",
                    "outcome": "negative",
                    "completion_summary": "The hypothesis was not supported.",
                    "completion_source": "documented_result",
                },
                "rationale": "The result file contains the final planned comparison.",
                "confidence": 0.95,
                "evidence": [{
                    "kind": "result_evidence",
                    "summary": "The final result records every planned metric.",
                    "artifact_id": artifact_id,
                }],
            }],
        },
    )
    assert proposed.status_code == 201, proposed.text
    proposal_id = proposed.json()["id"]
    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{proposal_id}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [operation_id],
        },
    )
    assert applied.status_code == 200, applied.text

    completed = _task(client, project["id"], task_id)
    completed_at = completed["completed_at"]
    assert completed["completion_actor"] == "Codex reconciliation"
    assert completed["completion_source"] == "documented_result"
    assert completed["completion_provenance"] == "agent"

    edited = mutate(
        client,
        project,
        applied.json()["semantic_revision"],
        [
            op(
                "task.update",
                {"title": "Run planned comparison (reviewed)"},
                task_id,
                completed["version"],
            ),
            op(
                "journal.create",
                {
                    "task_id": task_id,
                    "entry_type": "note",
                    "content": "Added a post-completion interpretation.",
                },
            ),
        ],
    )
    after_edit = _task(client, project["id"], task_id)
    assert after_edit["completed_at"] == completed_at
    assert after_edit["completion_actor"] == "Codex reconciliation"
    assert after_edit["completion_source"] == "documented_result"
    assert after_edit["completion_provenance"] == "agent"

    reopened = mutate(
        client,
        project,
        edited["semantic_revision"],
        [op("task.update", {"status": "in_progress"}, task_id, after_edit["version"])],
    )
    active = _task(client, project["id"], task_id)
    assert active["completed_at"] is None
    assert active["completion_actor"] is None
    assert active["completion_source"] is None
    assert active["completion_provenance"] is None
    assert active["completion_summary"] == ""

    mutate(
        client,
        project,
        reopened["semantic_revision"],
        [op(
            "task.update",
            {
                "status": "done",
                "completion_summary": "The rerun and analysis are complete.",
            },
            task_id,
            active["version"],
        )],
    )
    recompleted = _task(client, project["id"], task_id)
    assert recompleted["completed_at"] is not None
    assert recompleted["completed_at"] != completed_at
    assert recompleted["completion_actor"] == "test"
    assert recompleted["completion_source"] == "manual_confirmation"
    assert recompleted["completion_provenance"] == "manual"
