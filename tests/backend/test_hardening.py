from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from fastapi.testclient import TestClient
from research_monitor.api import create_app

import research_monitor.backup as backup_module
from research_monitor.backup import create_backup, restore_backup
from research_monitor.locking import ApplicationLock
from research_monitor.models import SourceReference
from research_monitor.service import DomainError

from .conftest import enroll, mutate
from .test_api import op


def test_vscode_forwarding_accepts_loopback_alias_and_port_remapping(
    settings, database,
) -> None:
    app = create_app(settings=settings, database=database)
    with TestClient(app, base_url="http://127.0.0.1:8765") as forwarded:
        numeric = forwarded.get("/", headers={"Host": "127.0.0.1:8765"})
        assert numeric.status_code == 200
        localhost = forwarded.get("/", headers={"Host": "localhost:8765"})
        assert localhost.status_code == 200
        remapped_port = forwarded.get("/", headers={"Host": "localhost:49152"})
        assert remapped_port.status_code == 200
        malformed = forwarded.get("/", headers={"Host": "localhost:not-a-port"})
        assert malformed.status_code == 400
        assert malformed.json()["detail"]["code"] == "invalid_host"
        remote = forwarded.get("/", headers={"Host": "research.example:8765"})
        assert remote.status_code == 400
        assert remote.json()["detail"]["code"] == "invalid_host"


def test_browser_mutation_requires_matching_csrf_and_exact_origin(client: TestClient, project_root: Path) -> None:
    landing = client.get("/")
    assert landing.status_code == 200
    csrf = client.cookies["research_monitor_csrf"]
    body = {"name": "Research", "root_path": str(project_root)}
    missing = client.post("/api/v1/projects", headers={"Origin": "http://testserver", "X-CSRF-Token": ""}, json=body)
    assert missing.status_code == 403
    wrong_origin = client.post("/api/v1/projects", headers={"Origin": "http://localhost:9999", "X-CSRF-Token": csrf}, json=body)
    assert wrong_origin.status_code == 403
    accepted = client.post("/api/v1/projects", headers={"Origin": "http://testserver", "X-CSRF-Token": csrf}, json=body)
    assert accepted.status_code == 201


def test_null_outcome_normalizes_and_completion_guard_is_recursive(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); pipeline_id, parent_id, child_id, leaf_id = [str(uuid4()) for _ in range(4)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P", "flow_mode": "freeform"}),
        op("task.create", {"id": parent_id, "pipeline_id": pipeline_id, "title": "Parent", "outcome": None}),
        op("task.create", {"id": child_id, "pipeline_id": pipeline_id, "parent_id": parent_id, "title": "Child"}),
        op("task.create", {"id": leaf_id, "pipeline_id": pipeline_id, "parent_id": child_id, "title": "Leaf"}),
    ])
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert next(task for task in snapshot["tasks"] if task["id"] == parent_id)["outcome"] == "not_applicable"
    child_done = mutate(client, project, created["semantic_revision"], [op("task.update", {"status": "done", "completion_summary": "Child wrapper closed", "completion_override_reason": "Leaf remains intentionally planned"}, child_id, 1)])
    response = client.post(f"/api/v1/projects/{project['id']}/mutations", json={"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": child_done["semantic_revision"], "actor_type": "ui", "operations": [op("task.update", {"status": "done", "completion_summary": "Parent done"}, parent_id, 1)]})
    assert response.status_code == 409


def test_parent_target_edges_expand_for_cycle_and_readiness(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); pipeline_id, blocker_id, parent_id, child_id = [str(uuid4()) for _ in range(4)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P", "flow_mode": "freeform"}),
        op("task.create", {"id": blocker_id, "pipeline_id": pipeline_id, "title": "Gate"}),
        op("task.create", {"id": parent_id, "pipeline_id": pipeline_id, "title": "Parent"}),
        op("task.create", {"id": child_id, "pipeline_id": pipeline_id, "parent_id": parent_id, "title": "Child"}),
        op("edge.create", {"source_task_id": blocker_id, "target_task_id": parent_id, "edge_type": "dependency"}),
        op("task.update", {"status": "in_progress"}, child_id, 1),
    ])
    child = next(task for task in client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["tasks"] if task["id"] == child_id)
    assert child["readiness"] == "inconsistent"
    cycle = client.post(f"/api/v1/projects/{project['id']}/mutations", json={"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": created["semantic_revision"], "actor_type": "ui", "operations": [op("edge.create", {"source_task_id": child_id, "target_task_id": parent_id, "edge_type": "dependency"})]})
    assert cycle.status_code == 422


def test_restore_reenables_valid_incident_edges(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); pipeline_id, a, b = [str(uuid4()) for _ in range(3)]
    created = mutate(client, project, 0, [op("pipeline.create", {"id": pipeline_id, "title": "P", "flow_mode": "freeform"}), op("task.create", {"id": a, "pipeline_id": pipeline_id, "title": "A"}), op("task.create", {"id": b, "pipeline_id": pipeline_id, "title": "B"}), op("edge.create", {"source_task_id": a, "target_task_id": b})])
    deleted = mutate(client, project, created["semantic_revision"], [op("task.delete", {}, a, 1)])
    restored = mutate(client, project, deleted["semantic_revision"], [op("task.restore", {}, a, 2)])
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert restored["semantic_revision"] == 3
    assert snapshot["edges"][0]["disabled"] is False


def test_proposal_dry_run_orders_prerequisites_and_persists_sources(client: TestClient, project_root: Path, database) -> None:
    project = enroll(client, project_root); pipeline_id, task_id = str(uuid4()), str(uuid4()); create_pipeline_id, create_task_id = str(uuid4()), str(uuid4())
    invalid = {"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": 0, "summary": "Invalid", "operations": [{"id": str(uuid4()), "type": "task.create", "data": {"id": str(uuid4()), "pipeline_id": str(uuid4()), "title": "Orphan"}}]}
    assert client.post(f"/api/v1/projects/{project['id']}/proposals/validate", json=invalid).status_code == 422
    proposal = {"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": 0, "summary": "Plan", "operations": [
        {"id": create_task_id, "type": "task.create", "data": {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}, "prerequisite_operation_ids": [create_pipeline_id], "rationale": "The plan defines this task", "confidence": 0.9, "source_references": [{"path": "PLAN.md", "anchor": "Task", "fingerprint": "abc"}]},
        {"id": create_pipeline_id, "type": "pipeline.create", "data": {"id": pipeline_id, "title": "Pipeline"}, "rationale": "The plan defines this pipeline", "confidence": 0.9, "evidence": [{"kind": "source_text", "locator": "PLAN.md#Pipeline"}]},
    ]}
    created = client.post(f"/api/v1/projects/{project['id']}/proposals", json=proposal)
    assert created.status_code == 201, created.text
    applied = client.post(f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply", json={"request_id": str(uuid4()), "selected_operation_ids": [create_task_id, create_pipeline_id]})
    assert applied.status_code == 200, applied.text
    with database.session() as session:
        references = session.query(SourceReference).filter_by(project_id=project["id"], task_id=task_id).all()
        assert [(item.source_path, item.anchor, item.fingerprint) for item in references] == [("PLAN.md", "Task", "abc")]


def test_agent_context_exposes_only_agent_authorized_operations(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root)
    # This endpoint is also required for server-mode CLI operation.
    response = client.get(f"/api/v1/projects/{project['id']}/agent-context")
    assert response.status_code == 200
    operation_types = set(response.json()["proposal_contract"]["operation_types"])
    assert not {"project.relink", "scan_policy.update", "artifact_root.create", "artifact_root.delete"} & operation_types


def test_lock_permissions_and_restore_rejects_incompatible_schema_without_replacement(settings, database, client: TestClient, project_root: Path, tmp_path: Path) -> None:
    project = enroll(client, project_root)
    lock = ApplicationLock(settings.lock_path)
    assert lock.acquire(); lock.release()
    assert settings.lock_path.stat().st_mode & 0o777 == 0o600
    backup = create_backup(database, tmp_path / "bad.db")
    connection = sqlite3.connect(backup)
    connection.execute("UPDATE schema_versions SET version=999"); connection.commit(); connection.close()
    with pytest.raises(DomainError) as error:
        restore_backup(database, backup, confirm=True)
    assert error.value.code == "schema_incompatible"
    with database.session() as session:
        assert session.query(SourceReference).count() == 0
        # Existing database remains compatible and retains enrolled project.
        assert session.execute(__import__("sqlalchemy").text("SELECT count(*) FROM projects")).scalar_one() == 1


def test_restore_rejects_incomplete_current_schema_before_live_replacement(
    client: TestClient, project_root: Path, database, tmp_path: Path
) -> None:
    project = enroll(client, project_root)
    incomplete = tmp_path / "incomplete.db"
    connection = sqlite3.connect(incomplete)
    connection.execute("CREATE TABLE schema_versions (version INTEGER PRIMARY KEY, applied_at TEXT)")
    connection.execute("INSERT INTO schema_versions(version, applied_at) VALUES (1, 'now')")
    connection.commit()
    connection.close()

    with pytest.raises(DomainError) as error:
        restore_backup(database, incomplete, confirm=True)
    assert error.value.code == "invalid_backup"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["project"]["id"] == project["id"]


def test_restore_rejects_forged_current_head_before_live_replacement(
    client: TestClient, project_root: Path, database, tmp_path: Path
) -> None:
    project = enroll(client, project_root)
    forged = tmp_path / "forged-current-head.db"
    connection = sqlite3.connect(forged)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_versions (
                version INTEGER PRIMARY KEY,
                applied_at DATETIME NOT NULL
            );
            INSERT INTO schema_versions(version, applied_at) VALUES (1, CURRENT_TIMESTAMP);
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL);
            INSERT INTO alembic_version(version_num) VALUES ('0004');
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DomainError) as error:
        restore_backup(database, forged, confirm=True)

    assert error.value.code == "invalid_backup"
    assert any(
        marker in str(error.value.details["reason"])
        for marker in ("missing ORM tables", "search trigger set")
    )
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["project"]["id"] == project["id"]
    assert not list(database.path.parent.glob("*.restore.tmp"))


def test_verified_backup_restore_succeeds_without_losing_snapshot_identity(
    client: TestClient, project_root: Path, database, tmp_path: Path
) -> None:
    project = enroll(client, project_root)
    backup = create_backup(database, tmp_path / "valid-restore.db")
    mutate(client, project, 0, [op("pipeline.create", {"title": "After backup"})])

    restore_backup(database, backup, confirm=True)

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["project"]["id"] == project["id"]
    assert snapshot.json()["pipelines"] == []


def test_restore_publication_fsync_failure_recovers_original_database(
    client: TestClient, project_root: Path, database, tmp_path: Path, monkeypatch
) -> None:
    project = enroll(client, project_root)
    backup = create_backup(database, tmp_path / "valid-before-fsync-failure.db")
    mutate(client, project, 0, [op("pipeline.create", {"title": "Keep after fsync failure"})])

    real_fsync_directory = backup_module.fsync_directory
    injected = False

    def fail_first_live_directory_sync(path: Path) -> None:
        nonlocal injected
        if Path(path) == database.path.parent and not injected:
            injected = True
            raise OSError("injected live-directory fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(backup_module, "fsync_directory", fail_first_live_directory_sync)
    with pytest.raises(DomainError) as error:
        restore_backup(database, backup, confirm=True)

    assert injected is True
    assert error.value.code == "restore_postcheck_failed"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot")
    assert snapshot.status_code == 200
    assert [item["title"] for item in snapshot.json()["pipelines"]] == [
        "Keep after fsync failure"
    ]


def test_restore_post_swap_integrity_failure_recovers_original_database(
    client: TestClient, project_root: Path, database, tmp_path: Path, monkeypatch
) -> None:
    project = enroll(client, project_root)
    backup = create_backup(database, tmp_path / "valid-before-postcheck.db")
    mutate(client, project, 0, [op("pipeline.create", {"title": "Preserve on rollback"})])

    checks = iter(["corrupt", "ok"])
    monkeypatch.setattr(database, "integrity_check", lambda: next(checks))
    with pytest.raises(DomainError) as error:
        restore_backup(database, backup, confirm=True)

    assert error.value.code == "restore_postcheck_failed"
    recovery_path = Path(error.value.details["recovery_backup"])
    assert recovery_path.is_file()
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot")
    assert snapshot.status_code == 200
    assert [item["title"] for item in snapshot.json()["pipelines"]] == [
        "Preserve on rollback"
    ]
