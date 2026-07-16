from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def test_snapshot_survives_missing_project_artifact_root(client: TestClient, project_root: Path) -> None:
    result_file = project_root / "result.txt"; result_file.write_text("result", encoding="utf-8")
    project = enroll(client, project_root); snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json(); artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {"id": artifact_id, "kind": "local", "artifact_root_id": snapshot["artifact_roots"][0]["id"], "locator": "result.txt", "label": "Result"})])
    moved = project_root.with_name("project-moved")
    project_root.rename(moved)
    response = client.get(f"/api/v1/projects/{project['id']}/snapshot")
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["project"]["unavailable"] is True
    assert value["artifacts"][0]["available"] is None
    metadata = client.get(f"/api/v1/artifacts/{artifact_id}/metadata")
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["available"] is False
    assert metadata.json()["previewable"] is False
