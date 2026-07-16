from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def test_stale_apply_closes_proposal_and_operations_as_conflicts(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    proposal_request = str(uuid4())
    operation_id = str(uuid4())
    pipeline_id = str(uuid4())
    proposal = {
        "api_version": "1",
        "schema_version": "1",
        "request_id": proposal_request,
        "project_id": project["id"],
        "base_semantic_revision": 0,
        "summary": "Create the documented plan",
        "operations": [
            {
                "id": operation_id,
                "type": "pipeline.create",
                "data": {"id": pipeline_id, "title": "Agent plan"},
                "rationale": "PLAN.md defines this workstream",
                "confidence": 0.9,
                "evidence": [{"path": "PLAN.md", "anchor": "Plan"}],
            }
        ],
    }
    created = client.post(f"/api/v1/projects/{project['id']}/proposals", json=proposal)
    assert created.status_code == 201, created.text

    mutate(client, project, 0, [op("pipeline.create", {"title": "Manual change"})])
    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply",
        json={"request_id": str(uuid4()), "selected_operation_ids": [operation_id]},
    )
    assert applied.status_code == 409

    refreshed = client.get(f"/api/v1/proposals/{created.json()['id']}")
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "conflict"
    assert refreshed.json()["closed_at"] is not None
    assert refreshed.json()["operations"][0]["disposition"] == "conflict"
