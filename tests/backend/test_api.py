from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from research_monitor.api import create_app
from research_monitor.backup import create_backup, restore_backup
from research_monitor.config import Settings
from research_monitor.database import Database
from research_monitor.serializers import canonical_json
from .conftest import enroll, mutate


def op(operation_type: str, data: dict, entity_id: str | None = None, expected_version: int | None = None) -> dict:
    value = {"id": str(uuid4()), "type": operation_type, "data": data}
    if entity_id: value["entity_id"] = entity_id
    if expected_version: value["expected_version"] = expected_version
    return value


def test_enrollment_snapshot_and_nested_task_editing(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    pipeline_id, parent_id, child_id = str(uuid4()), str(uuid4()), str(uuid4())
    result = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Data", "flow_mode": "sequential"}),
            op("task.create", {"id": parent_id, "pipeline_id": pipeline_id, "title": "Prepare", "child_flow_mode": "sequential"}),
            op("task.create", {"id": child_id, "pipeline_id": pipeline_id, "parent_id": parent_id, "title": "Download"}),
        ],
    )
    assert result["semantic_revision"] == 1
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["availability"] == "available"
    assert {item["id"] for item in snapshot["tasks"]} == {parent_id, child_id}
    assert snapshot["progress"]["leaf_total"] == 1
    child = next(item for item in snapshot["tasks"] if item["id"] == child_id)
    assert child["readiness"] == "ready"
    assert child["version"] == 1


def test_status_guards_and_recursive_parent_completion(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    pipeline_id, parent_id, child_id, grandchild_id = [str(uuid4()) for _ in range(4)]
    first = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "Experiments"}),
        op("task.create", {"id": parent_id, "pipeline_id": pipeline_id, "title": "Study"}),
        op("task.create", {"id": child_id, "pipeline_id": pipeline_id, "parent_id": parent_id, "title": "Baseline"}),
        op("task.create", {"id": grandchild_id, "pipeline_id": pipeline_id, "parent_id": child_id, "title": "Seed 1"}),
    ])
    blocked = client.post(f"/api/v1/projects/{project['id']}/mutations", json={
        "api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"],
        "base_semantic_revision": first["semantic_revision"], "actor_type": "ui",
        "operations": [op("task.update", {"status": "blocked"}, parent_id, 1)],
    })
    assert blocked.status_code == 422
    completed = client.post(f"/api/v1/projects/{project['id']}/mutations", json={
        "api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"],
        "base_semantic_revision": first["semantic_revision"], "actor_type": "ui",
        "operations": [op("task.update", {"status": "done", "completion_summary": "Finished"}, parent_id, 1)],
    })
    assert completed.status_code == 409


def test_idempotent_mutation_and_revision_conflict(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    request_id = str(uuid4()); pipeline_id = str(uuid4())
    body = {"api_version": "1", "schema_version": "1", "request_id": request_id, "project_id": project["id"], "base_semantic_revision": 0, "actor_type": "ui", "operations": [op("pipeline.create", {"id": pipeline_id, "title": "Analysis"})]}
    first = client.post(f"/api/v1/projects/{project['id']}/mutations", json=body)
    second = client.post(f"/api/v1/projects/{project['id']}/mutations", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    stale = dict(body); stale["request_id"] = str(uuid4())
    response = client.post(f"/api/v1/projects/{project['id']}/mutations", json=stale)
    assert response.status_code == 409


def test_artifact_preview_and_url_safety(client: TestClient, project_root: Path) -> None:
    (project_root / "result.json").write_text('{"score": 0.9}', encoding="utf-8")
    (project_root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    project = enroll(client, project_root)
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    root_id = snapshot["artifact_roots"][0]["id"]
    result_id, secret_id = str(uuid4()), str(uuid4())
    changed = mutate(client, project, 0, [
        op("artifact.create", {"id": result_id, "kind": "local", "artifact_root_id": root_id, "locator": "result.json", "label": "Metrics"}),
        op("artifact.create", {"id": secret_id, "kind": "local", "artifact_root_id": root_id, "locator": ".env", "label": "No"}),
    ])
    preview = client.get(f"/api/v1/artifacts/{result_id}/preview")
    assert preview.json() == {"score": 0.9}
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in preview.headers["content-security-policy"]
    assert client.get(f"/api/v1/artifacts/{secret_id}/preview").status_code == 415
    unsafe = client.post(f"/api/v1/projects/{project['id']}/mutations", json={
        "api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"],
        "base_semantic_revision": changed["semantic_revision"], "actor_type": "ui",
        "operations": [op("artifact.create", {"kind": "url", "locator": "file:///etc/passwd", "label": "bad"})],
    })
    assert unsafe.status_code == 422



def test_mutation_request_id_rejects_changed_payload(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    request_id, pipeline_id, operation_id = str(uuid4()), str(uuid4()), str(uuid4())
    body = {
        "api_version": "1", "schema_version": "1", "request_id": request_id,
        "project_id": project["id"], "base_semantic_revision": 0, "actor_type": "ui",
        "operations": [{"id": operation_id, "type": "pipeline.create", "data": {"id": pipeline_id, "title": "Original"}}],
    }
    first = client.post(f"/api/v1/projects/{project['id']}/mutations", json=body)
    changed = {**body, "operations": [{"id": operation_id, "type": "pipeline.create", "data": {"id": pipeline_id, "title": "Changed"}}]}
    collision = client.post(f"/api/v1/projects/{project['id']}/mutations", json=changed)

    assert first.status_code == 200
    assert collision.status_code == 409
    assert collision.json()["detail"]["code"] == "idempotency_collision"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["pipelines"][0]["title"] == "Original"


def test_project_export_collections_are_deterministic(client: TestClient, project_root: Path) -> None:
    (project_root / "result.json").write_text('{"score": 1}', encoding="utf-8")
    project = enroll(client, project_root)
    root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"][0]["id"]
    pipeline_id, first_task, second_task, artifact_id = [str(uuid4()) for _ in range(4)]
    changed = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "Pipeline"}),
        op("task.create", {"id": second_task, "pipeline_id": pipeline_id, "title": "Second", "position": 2}),
        op("task.create", {"id": first_task, "pipeline_id": pipeline_id, "title": "First", "position": 1}),
        op("edge.create", {"source_task_id": first_task, "target_task_id": second_task}),
        op("journal.create", {"task_id": first_task, "content": "Progress"}),
        op("artifact.create", {"id": artifact_id, "kind": "local", "artifact_root_id": root_id, "locator": "result.json", "label": "Result"}),
        op("task_artifact.link", {"task_id": second_task, "artifact_id": artifact_id, "role": "result"}),
    ])
    layout = client.post(
        f"/api/v1/projects/{project['id']}/layout-mutations",
        json={
            "api_version": "1", "schema_version": "1", "request_id": str(uuid4()),
            "project_id": project["id"], "base_layout_revision": 0, "actor_type": "ui",
            "operations": [op("layout.upsert", {"task_id": first_task, "x": 10, "y": 20})],
        },
    )
    assert changed["semantic_revision"] == 1
    assert layout.status_code == 200

    first = client.get(f"/api/v1/projects/{project['id']}/export").json()
    second = client.get(f"/api/v1/projects/{project['id']}/export").json()
    assert canonical_json(first) == canonical_json(second)
    exported = first["project"]
    for collection in ("artifact_roots", "pipelines", "tasks", "edges", "journals", "artifacts", "task_artifacts", "layouts"):
        assert exported[collection] == sorted(exported[collection], key=canonical_json)


def test_outbox_semantic_events_use_result_revision(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    changed = mutate(client, project, 0, [
        op("pipeline.create", {"id": str(uuid4()), "title": "Pipeline"}),
    ])
    assert changed["semantic_revision"] == 1

    response = client.get("/api/v1/events")
    body = response.json()
    events = body["events"]
    enrolled = next(event for event in events if event["event_type"] == "project.enroll")
    pipeline = next(event for event in events if event["event_type"] == "pipeline.create")
    assert enrolled["payload"]["revision"] == 0
    assert pipeline["payload"]["revision"] == 1
    assert body["latest_id"] == max(event["id"] for event in events)
    assert body["reset_required"] is False
    assert body["reset_reason"] is None
    assert response.headers["X-Research-Monitor-Event-Stream-Id"] == body["stream_id"]
    assert response.headers["X-Research-Monitor-Event-Latest-Id"] == str(body["latest_id"])
    assert response.headers["X-Research-Monitor-Event-Reset"] == "false"

    cursor_ahead = client.get("/api/v1/events", params={"after": body["latest_id"] + 1}).json()
    assert cursor_ahead["reset_required"] is True
    assert cursor_ahead["reset_reason"] == "cursor_ahead"
    assert cursor_ahead["events"] == events

    non_ascii_stream = client.get("/api/v1/events", params={"stream_id": "é"})
    assert non_ascii_stream.status_code == 200
    assert non_ascii_stream.json()["reset_required"] is True
    assert non_ascii_stream.json()["reset_reason"] == "stream_changed"


def test_event_stream_generation_resets_replay_after_supported_restore(
    settings: Settings,
    database: Database,
    project_root: Path,
) -> None:
    headers = {"Authorization": f"Bearer {settings.cli_token}"}
    with TestClient(create_app(settings=settings, database=database), headers=headers) as original:
        project = enroll(original, project_root)
        backup = create_backup(database)
        mutate(original, project, 0, [
            op("pipeline.create", {"id": str(uuid4()), "title": "After backup"}),
        ])
        before_restore = original.get("/api/v1/events").json()
        assert before_restore["latest_id"] == max(event["id"] for event in before_restore["events"])

    restore_backup(database, backup, confirm=True)

    with TestClient(create_app(settings=settings, database=database), headers=headers) as restarted:
        replay_response = restarted.get("/api/v1/events", params={
            "after": before_restore["latest_id"],
            "stream_id": before_restore["stream_id"],
        })
        replay = replay_response.json()

        assert replay["stream_id"] != before_restore["stream_id"]
        assert replay["latest_id"] < before_restore["latest_id"]
        assert replay["reset_required"] is True
        assert replay["reset_reason"] == "stream_changed"
        assert replay["events"]
        assert max(event["id"] for event in replay["events"]) == replay["latest_id"]
        assert replay_response.headers["X-Research-Monitor-Event-Reset"] == "true"

        caught_up = restarted.get("/api/v1/events", params={
            "after": replay["latest_id"],
            "stream_id": replay["stream_id"],
        }).json()
        assert caught_up == {
            "events": [],
            "stream_id": replay["stream_id"],
            "latest_id": replay["latest_id"],
            "reset_required": False,
            "reset_reason": None,
        }
