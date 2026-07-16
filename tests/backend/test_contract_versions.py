from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll
from .test_api import op


def test_all_versioned_write_contracts_reject_incompatible_versions(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)

    mutation = {
        "api_version": "999",
        "schema_version": "1",
        "request_id": str(uuid4()),
        "project_id": project["id"],
        "base_semantic_revision": 0,
        "operations": [op("pipeline.create", {"title": "Must not be created"})],
    }
    response = client.post(f"/api/v1/projects/{project['id']}/mutations", json=mutation)
    assert response.status_code == 422

    layout = {
        "api_version": "1",
        "schema_version": "999",
        "request_id": str(uuid4()),
        "project_id": project["id"],
        "base_layout_revision": 0,
        "operations": [op("layout.upsert", {"task_id": str(uuid4()), "x": 0, "y": 0})],
    }
    response = client.post(f"/api/v1/projects/{project['id']}/layout-mutations", json=layout)
    assert response.status_code == 422

    proposal = {
        "api_version": "999",
        "schema_version": "1",
        "request_id": str(uuid4()),
        "project_id": project["id"],
        "base_semantic_revision": 0,
        "summary": "Must not be accepted",
        "operations": [op("pipeline.create", {"title": "Must not be created"})],
    }
    response = client.post(f"/api/v1/projects/{project['id']}/proposals/validate", json=proposal)
    assert response.status_code == 422
