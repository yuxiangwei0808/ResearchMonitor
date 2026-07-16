from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def test_unarchiving_pipeline_does_not_restore_previously_deleted_tasks(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, kept_id, deleted_id = [str(uuid4()) for _ in range(3)]
    created = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op("task.create", {"id": kept_id, "pipeline_id": pipeline_id, "title": "Keep"}),
            op("task.create", {"id": deleted_id, "pipeline_id": pipeline_id, "title": "Delete"}),
        ],
    )
    deleted = mutate(
        client,
        project,
        created["semantic_revision"],
        [op("task.delete", {}, deleted_id, 1)],
    )
    archived = mutate(
        client,
        project,
        deleted["semantic_revision"],
        [op("pipeline.archive", {}, pipeline_id, 1)],
    )
    mutate(
        client,
        project,
        archived["semantic_revision"],
        [op("pipeline.restore", {}, pipeline_id, 2)],
    )

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    pipeline = next(item for item in snapshot["pipelines"] if item["id"] == pipeline_id)
    deleted_task = next(item for item in snapshot["tasks"] if item["id"] == deleted_id)
    assert pipeline["archived"] is False
    assert deleted_task["deleted_at"] is not None


def test_pipeline_restore_only_revives_rows_and_edges_from_its_delete_batch(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, external_pipeline_id, kept_id, predeleted_id, external_id = [
        str(uuid4()) for _ in range(5)
    ]
    kept_edge_id, predeleted_edge_id = str(uuid4()), str(uuid4())
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "Work", "flow_mode": "freeform"}),
        op("pipeline.create", {"id": external_pipeline_id, "title": "External", "flow_mode": "freeform"}),
        op("task.create", {"id": kept_id, "pipeline_id": pipeline_id, "title": "Restore me"}),
        op("task.create", {"id": predeleted_id, "pipeline_id": pipeline_id, "title": "Keep deleted"}),
        op("task.create", {"id": external_id, "pipeline_id": external_pipeline_id, "title": "Outside"}),
        op("edge.create", {
            "id": kept_edge_id, "source_task_id": kept_id,
            "target_task_id": external_id, "edge_type": "related",
        }),
        op("edge.create", {
            "id": predeleted_edge_id, "source_task_id": predeleted_id,
            "target_task_id": external_id, "edge_type": "related",
        }),
    ])
    predeleted = mutate(client, project, created["semantic_revision"], [
        op("task.delete", {}, predeleted_id, 1),
    ])
    pipeline_deleted = mutate(client, project, predeleted["semantic_revision"], [
        op("pipeline.delete", {"cascade": True}, pipeline_id, 1),
    ])
    restored = mutate(client, project, pipeline_deleted["semantic_revision"], [
        op("pipeline.restore", {}, pipeline_id, 2),
    ])

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    tasks = {item["id"]: item for item in snapshot["tasks"]}
    edges = {item["id"]: item for item in snapshot["edges"]}
    assert restored["semantic_revision"] == 4
    assert tasks[kept_id]["deleted_at"] is None
    assert tasks[predeleted_id]["deleted_at"] is not None
    assert edges[kept_edge_id]["disabled"] is False
    assert edges[predeleted_edge_id]["disabled"] is True
    assert edges[predeleted_edge_id]["disabled_reason"] == "subtree_deleted"
