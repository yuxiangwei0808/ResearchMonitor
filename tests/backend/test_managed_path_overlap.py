from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from research_monitor.backup import create_backup
from research_monitor.service import DomainError, _validate_monitor_storage_separation

from .conftest import enroll
from .test_api import op


def _mutation(project: dict, operation: dict, revision: int = 0) -> dict:
    return {
        "api_version": "1",
        "schema_version": "1",
        "request_id": str(uuid4()),
        "project_id": project["id"],
        "base_semantic_revision": revision,
        "actor_type": "ui",
        "operations": [operation],
    }


def test_enrollment_rejects_roots_inside_or_containing_monitor_storage(
    client: TestClient, settings, database, tmp_path: Path
) -> None:
    nested = settings.data_dir / "nested-research"
    nested.mkdir()

    for candidate in (settings.data_dir, nested, tmp_path):
        response = client.post(
            "/api/v1/projects",
            json={"name": "Unsafe", "root_path": str(candidate)},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "root_overlaps_monitor_storage"

    assert client.get("/api/v1/projects").json()["projects"] == []
    # Failed enrollment cannot poison the managed recovery destination.
    backup = create_backup(database)
    assert backup.is_file()
    assert backup.parent == settings.database_path.parent / "backups"


def test_relink_rejects_monitor_storage_and_preserves_existing_root(
    client: TestClient, project_root: Path, settings, database
) -> None:
    project = enroll(client, project_root)
    response = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json=_mutation(
            project,
            op("project.relink", {"root_path": str(settings.config_dir)}, project["id"]),
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "root_overlaps_monitor_storage"

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["root_path"] == str(project_root.resolve())
    assert snapshot["project"]["semantic_revision"] == 0
    assert create_backup(database).is_file()


def test_additional_artifact_root_rejects_monitor_runtime_storage(
    client: TestClient, project_root: Path, settings
) -> None:
    project = enroll(client, project_root)
    response = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json=_mutation(
            project,
            op("artifact_root.create", {
                "id": str(uuid4()),
                "name": "Unsafe runtime root",
                "canonical_path": str(settings.runtime_dir),
            }),
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "root_overlaps_monitor_storage"

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert len(snapshot["artifact_roots"]) == 1
    assert snapshot["project"]["semantic_revision"] == 0


def test_each_separate_monitor_area_is_reserved(settings, tmp_path: Path) -> None:
    data_dir = tmp_path / "monitor-data"
    config_dir = tmp_path / "monitor-config"
    runtime_dir = tmp_path / "monitor-runtime"
    database_dir = tmp_path / "monitor-database"
    backup_dir = database_dir / "backups"
    safe_root = tmp_path / "research"
    for directory in (data_dir, config_dir, runtime_dir, database_dir, backup_dir, safe_root):
        directory.mkdir(parents=True, exist_ok=True)

    separated = replace(
        settings,
        data_dir=data_dir,
        config_dir=config_dir,
        runtime_dir=runtime_dir,
        database_path=database_dir / "monitor.db",
        config_path=config_dir / "config.toml",
        runtime_descriptor=runtime_dir / "server.json",
        lock_path=runtime_dir / "app.lock",
    )
    _validate_monitor_storage_separation(safe_root, separated)

    candidates = (
        data_dir / "nested",
        config_dir,
        runtime_dir,
        database_dir,
        backup_dir,
        tmp_path,
    )
    (data_dir / "nested").mkdir()
    for candidate in candidates:
        with pytest.raises(DomainError) as error:
            _validate_monitor_storage_separation(candidate, separated)
        assert error.value.code == "root_overlaps_monitor_storage"
