from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from research_monitor.lifecycle import purge_project
from research_monitor.service import DomainError

from .conftest import enroll, mutate
from .test_api import op


def test_nested_project_resolution_and_unavailable_relink(client: TestClient, project_root: Path) -> None:
    nested = project_root / "nested"; nested.mkdir()
    parent = enroll(client, project_root)
    response = client.post("/api/v1/projects", json={"name": "Nested", "root_path": str(nested)})
    assert response.status_code == 201; child = response.json()["project"]
    resolved = client.get("/api/v1/projects/resolve", params={"path": str(nested / "new-file.txt")})
    # Resolution requires the path itself to exist.
    assert resolved.status_code == 422
    file = nested / "new-file.txt"; file.write_text("x", encoding="utf-8")
    assert client.get("/api/v1/projects/resolve", params={"path": str(file)}).json()["id"] == child["id"]
    assert client.get("/api/v1/projects/resolve", params={"path": str(project_root)}).json()["id"] == parent["id"]


def test_permanent_purge_requires_trash_confirmation_and_creates_backup(client: TestClient, project_root: Path, database) -> None:
    project = enroll(client, project_root)
    try:
        purge_project(database, project["id"], confirm=project["id"])
    except DomainError as exc:
        assert exc.code == "project_not_trashed"
    mutate(client, project, 0, [op("project.trash", {}, project["id"])])
    result = purge_project(database, project["id"], confirm=project["id"])
    assert result["purged"] is True
    assert Path(result["backup_path"]).is_file()
    response = client.get(f"/api/v1/projects/{project['id']}/snapshot")
    assert response.status_code == 404
    assert project_root.is_dir()
