from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def test_archived_pipeline_tasks_do_not_gate_visible_task_readiness(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    archived_pipeline, active_pipeline, hidden_task, visible_task = [str(uuid4()) for _ in range(4)]
    created = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": archived_pipeline, "title": "Old work", "flow_mode": "freeform"}),
            op("pipeline.create", {"id": active_pipeline, "title": "Current work", "flow_mode": "freeform"}),
            op("task.create", {"id": hidden_task, "pipeline_id": archived_pipeline, "title": "Hidden prerequisite"}),
            op("task.create", {"id": visible_task, "pipeline_id": active_pipeline, "title": "Visible task"}),
            op("edge.create", {"source_task_id": hidden_task, "target_task_id": visible_task, "edge_type": "dependency"}),
        ],
    )
    before = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert next(task for task in before["tasks"] if task["id"] == visible_task)["readiness"] == "waiting"

    mutate(
        client,
        project,
        created["semantic_revision"],
        [op("pipeline.archive", {}, archived_pipeline, 1)],
    )
    after = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    visible = next(task for task in after["tasks"] if task["id"] == visible_task)
    assert visible["readiness"] == "ready"
    assert after["progress"]["waiting"] == 0
    assert after["progress"]["ready"] == 1
