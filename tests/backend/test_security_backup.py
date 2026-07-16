from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


from research_monitor.backup import create_backup

from research_monitor.service import DomainError
from .conftest import enroll, mutate
from .test_api import op


def test_security_headers_and_cross_origin_rejection(client: TestClient, project_root: Path) -> None:
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    rejected = client.post("/api/v1/projects", headers={"Origin": "https://evil.example"}, json={"name": "bad", "root_path": str(project_root)})
    assert rejected.status_code == 403
    rejected_host = client.get("/api/v1/projects", headers={"Host": "evil.example"})
    assert rejected_host.status_code == 400


def test_backup_uses_consistent_sqlite_copy(client: TestClient, project_root: Path, database) -> None:
    enroll(client, project_root)
    backup = create_backup(database)
    assert backup.is_file()
    assert database.integrity_check(backup) == "ok"


def test_backup_rejects_live_sqlite_files_and_enrolled_roots(
    client: TestClient, project_root: Path, database
) -> None:
    enroll(client, project_root)
    forbidden = [
        database.path,
        Path(f"{database.path}-wal"),
        Path(f"{database.path}-shm"),
        Path(f"{database.path}-journal"),
        project_root / "monitor-backup.db",
    ]
    for target in forbidden:
        with pytest.raises(DomainError) as error:
            create_backup(database, target)
        assert error.value.code in {"unsafe_backup_target", "backup_target_in_project"}

    response = client.post("/api/v1/backup", json={"output": str(project_root / "api-backup.db")})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "backup_target_in_project"


def test_backup_no_force_never_replaces_target_created_during_publication(
    database, tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "raced.db"
    real_link = os.link

    def competing_publish(source, destination, *args, **kwargs):
        Path(destination).write_bytes(b"other-process")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "link", competing_publish)
    with pytest.raises(DomainError) as error:
        create_backup(database, target)

    assert error.value.code == "backup_target_exists"
    assert target.read_bytes() == b"other-process"
    assert not list(tmp_path.glob(f".{target.name}.*.tmp"))


def test_export_guard_rejects_approved_artifact_roots(
    client: TestClient, project_root: Path, database, tmp_path: Path
) -> None:
    project = enroll(client, project_root)
    approved = tmp_path / "approved-results"
    approved.mkdir()
    mutate(client, project, 0, [op("artifact_root.create", {
        "id": "4f0819aa-29e4-4ff7-b46f-39d59e7a17c1",
        "name": "Approved results",
        "canonical_path": str(approved),
    })])

    response = client.get(
        f"/api/v1/projects/{project['id']}/export",
        params={"output_path": str(approved / "portable.json")},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "export_target_in_project"
    assert not (approved / "portable.json").exists()
