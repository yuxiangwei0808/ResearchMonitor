from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def test_dependency_cycle_is_atomic_and_waiver_satisfies_dropped_dependency(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); pipeline_id, a, b = [str(uuid4()) for _ in range(3)]
    changed = mutate(client, project, 0, [op("pipeline.create", {"id": pipeline_id, "title": "P", "flow_mode": "freeform"}), op("task.create", {"id": a, "pipeline_id": pipeline_id, "title": "A"}), op("task.create", {"id": b, "pipeline_id": pipeline_id, "title": "B"}), op("edge.create", {"source_task_id": a, "target_task_id": b, "edge_type": "dependency"})])
    cycle = client.post(f"/api/v1/projects/{project['id']}/mutations", json={"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": changed["semantic_revision"], "actor_type": "ui", "operations": [op("edge.create", {"source_task_id": b, "target_task_id": a, "edge_type": "dependency"})]})
    assert cycle.status_code == 422
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert len([edge for edge in snapshot["edges"] if not edge["deleted_at"]]) == 1


def test_proposal_authority_deduplication_and_selective_apply(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); pipeline_id = str(uuid4()); op_id = str(uuid4()); request_id = str(uuid4())
    proposal = {"api_version": "1", "schema_version": "1", "request_id": request_id, "project_id": project["id"], "base_semantic_revision": 0, "summary": "Draft plan", "operations": [{"id": op_id, "type": "pipeline.create", "data": {"id": pipeline_id, "title": "Plan"}, "rationale": "Document heading", "confidence": 0.9, "evidence": [{"kind": "document", "locator": "PLAN.md"}], "source_references": [{"path": "PLAN.md", "anchor": "Plan"}]}]}
    created = client.post(f"/api/v1/projects/{project['id']}/proposals", json=proposal)
    assert created.status_code == 201, created.text
    duplicate = dict(proposal); duplicate["request_id"] = str(uuid4()); duplicate["operations"] = [dict(proposal["operations"][0], id=str(uuid4()))]
    duplicated = client.post(f"/api/v1/projects/{project['id']}/proposals", json=duplicate)
    assert duplicated.json()["id"] == created.json()["id"]
    applied = client.post(f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply", json={"request_id": str(uuid4()), "selected_operation_ids": [op_id]})
    assert applied.status_code == 200, applied.text
    assert applied.json()["semantic_revision"] == 1
    prohibited = dict(proposal); prohibited["request_id"] = str(uuid4()); prohibited["base_semantic_revision"] = 1; prohibited["operations"] = [{"id": str(uuid4()), "type": "scan_policy.update", "data": {"include_globs": ["**/*"]}}]
    response = client.post(f"/api/v1/projects/{project['id']}/proposals/validate", json=prohibited)
    assert response.status_code == 403


def test_layout_revision_does_not_stale_semantic_proposal(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); pipeline_id, task_id = str(uuid4()), str(uuid4())
    changed = mutate(client, project, 0, [op("pipeline.create", {"id": pipeline_id, "title": "P"}), op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "T"})])
    body = {"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_layout_revision": 0, "actor_type": "ui", "operations": [op("layout.upsert", {"task_id": task_id, "x": 10, "y": 20})]}
    layout = client.post(f"/api/v1/projects/{project['id']}/layout-mutations", json=body)
    assert layout.status_code == 200
    assert layout.json()["semantic_revision"] == changed["semantic_revision"]



def test_deleted_edge_can_be_recreated_by_reviving_its_tombstone(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, first_task, second_task, edge_id = [str(uuid4()) for _ in range(4)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P", "flow_mode": "freeform"}),
        op("task.create", {"id": first_task, "pipeline_id": pipeline_id, "title": "A"}),
        op("task.create", {"id": second_task, "pipeline_id": pipeline_id, "title": "B"}),
        op("edge.create", {"id": edge_id, "source_task_id": first_task, "target_task_id": second_task}),
    ])
    deleted = mutate(client, project, created["semantic_revision"], [op("edge.delete", {}, edge_id, 1)])
    recreated = mutate(client, project, deleted["semantic_revision"], [op(
        "edge.create",
        {"id": str(uuid4()), "source_task_id": first_task, "target_task_id": second_task},
    )])

    assert recreated["results"][0]["entity_id"] == edge_id
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    matching = [edge for edge in snapshot["edges"] if edge["source_task_id"] == first_task and edge["target_task_id"] == second_task]
    assert len(matching) == 1
    assert matching[0]["deleted_at"] is None
    assert matching[0]["version"] == 3


def test_related_edges_are_undirected_and_reverse_duplicates_are_rejected(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, first_task, second_task = [str(uuid4()) for _ in range(3)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P", "flow_mode": "freeform"}),
        op("task.create", {"id": first_task, "pipeline_id": pipeline_id, "title": "A"}),
        op("task.create", {"id": second_task, "pipeline_id": pipeline_id, "title": "B"}),
        op("edge.create", {
            "id": str(uuid4()),
            "source_task_id": second_task,
            "target_task_id": first_task,
            "edge_type": "related",
        }),
    ])

    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": created["semantic_revision"],
            "actor_type": "ui",
            "operations": [op("edge.create", {
                "id": str(uuid4()),
                "source_task_id": first_task,
                "target_task_id": second_task,
                "edge_type": "related",
            })],
        },
    )

    assert duplicate.status_code in {409, 422}
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    related = [
        edge for edge in snapshot["edges"]
        if edge["edge_type"] == "related" and not edge["deleted_at"]
    ]
    assert len(related) == 1
    assert [related[0]["source_task_id"], related[0]["target_task_id"]] == sorted(
        [first_task, second_task]
    )


def test_viewport_upsert_is_persisted_as_layout_only_state(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    viewport_id = str(uuid4())
    first = client.post(
        f"/api/v1/projects/{project['id']}/layout-mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_layout_revision": 0,
            "actor_type": "ui",
            "operations": [op("viewport.upsert", {
                "id": viewport_id, "x": 120, "y": -40, "zoom": 1.75,
            })],
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["semantic_revision"] == 0
    assert first.json()["layout_revision"] == 1
    stored_id = first.json()["results"][0]["entity_id"]
    assert stored_id != viewport_id

    second = client.post(
        f"/api/v1/projects/{project['id']}/layout-mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_layout_revision": 1,
            "actor_type": "ui",
            "operations": [op(
                "viewport.upsert", {"x": 200, "zoom": 2}, stored_id, 1,
            )],
        },
    )
    assert second.status_code == 200, second.text
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["project"]["layout_revision"] == 2
    assert snapshot["viewports"] == [{
        "id": stored_id,
        "parent_id": None,
        "x": 200.0,
        "y": -40.0,
        "zoom": 2.0,
        "version": 2,
    }]


def test_same_task_can_have_distinct_layout_rows_after_scope_move(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, parent_id, task_id = [str(uuid4()) for _ in range(3)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P"}),
        op("task.create", {"id": parent_id, "pipeline_id": pipeline_id, "title": "Parent"}),
        op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Moved"}),
    ])

    def save_layout(base_revision: int, parent: str | None) -> dict:
        data = {"id": task_id, "task_id": task_id, "parent_id": parent, "x": 10, "y": 20}
        response = client.post(
            f"/api/v1/projects/{project['id']}/layout-mutations",
            json={
                "api_version": "1", "schema_version": "1",
                "request_id": str(uuid4()), "project_id": project["id"],
                "base_layout_revision": base_revision, "actor_type": "ui",
                "operations": [op("layout.upsert", data)],
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    root_layout = save_layout(0, None)
    mutate(client, project, created["semantic_revision"], [op(
        "task.move", {"parent_id": parent_id}, task_id, 1,
    )])
    child_layout = save_layout(root_layout["layout_revision"], parent_id)

    assert root_layout["results"][0]["entity_id"] != child_layout["results"][0]["entity_id"]
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    layouts = [item for item in snapshot["layouts"] if item["task_id"] == task_id]
    assert {item["parent_id"] for item in layouts} == {None, parent_id}
    assert len({item["id"] for item in layouts}) == 2
