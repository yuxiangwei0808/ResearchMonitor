from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def test_dropped_explicit_dependency_requires_reasoned_waiver(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, dropped_id, target_id = [str(uuid4()) for _ in range(3)]
    created = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work", "flow_mode": "freeform"}),
            op("task.create", {"id": dropped_id, "pipeline_id": pipeline_id, "title": "Abandoned", "status": "dropped"}),
            op("task.create", {"id": target_id, "pipeline_id": pipeline_id, "title": "Next"}),
            op("edge.create", {"source_task_id": dropped_id, "target_task_id": target_id, "edge_type": "dependency"}),
        ],
    )
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    target = next(task for task in snapshot["tasks"] if task["id"] == target_id)
    edge = snapshot["edges"][0]
    assert target["readiness"] == "waiting"

    mutate(
        client,
        project,
        created["semantic_revision"],
        [op("edge.update", {"waiver_reason": "Approach was intentionally abandoned"}, edge["id"], edge["version"])],
    )
    refreshed = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert next(task for task in refreshed["tasks"] if task["id"] == target_id)["readiness"] == "ready"
    assert refreshed["edges"][0]["waived"] is True


def test_sequential_flow_skips_dropped_sibling_and_done_task_records_negative_outcome(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, first_id, dropped_id, last_id = [str(uuid4()) for _ in range(4)]
    created = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Experiments", "flow_mode": "sequential"}),
            op("task.create", {"id": first_id, "pipeline_id": pipeline_id, "title": "Run", "position": 0}),
            op("task.create", {"id": dropped_id, "pipeline_id": pipeline_id, "title": "Obsolete", "status": "dropped", "position": 1}),
            op("task.create", {"id": last_id, "pipeline_id": pipeline_id, "title": "Analyze", "position": 2}),
        ],
    )
    before = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    last = next(task for task in before["tasks"] if task["id"] == last_id)
    assert last["readiness"] == "waiting"
    assert last["unsatisfied_predecessor_ids"] == [first_id]

    mutate(
        client,
        project,
        created["semantic_revision"],
        [
            op(
                "task.update",
                {"status": "done", "outcome": "negative", "completion_summary": "The hypothesis was not supported"},
                first_id,
                1,
            )
        ],
    )
    after = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    first = next(task for task in after["tasks"] if task["id"] == first_id)
    last = next(task for task in after["tasks"] if task["id"] == last_id)
    assert first["status"] == "done"
    assert first["outcome"] == "negative"
    assert first["completed_at"] is not None
    assert first["completion_provenance"] == "manual"
    assert last["readiness"] == "ready"
