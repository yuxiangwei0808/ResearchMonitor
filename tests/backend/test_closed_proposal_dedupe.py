from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from .conftest import enroll


def _proposal(project_id: str, base_revision: int) -> tuple[dict, str]:
    operation_id = str(uuid4())
    return (
        {
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project_id,
            "base_semantic_revision": base_revision,
            "summary": "Create the source-backed plan",
            "operations": [
                {
                    "id": operation_id,
                    "type": "pipeline.create",
                    "data": {"id": str(uuid4()), "title": "Source-backed plan"},
                    "rationale": "PLAN.md defines this workstream",
                    "confidence": 0.95,
                    "evidence": [{"path": "PLAN.md", "anchor": "Plan"}],
                    "source_references": [{"path": "PLAN.md", "anchor": "Plan", "fingerprint": "sha256:unchanged"}],
                }
            ],
        },
        operation_id,
    )


@pytest.mark.parametrize("closure", ["apply", "reject"])
def test_unchanged_semantic_resubmission_reuses_closed_proposal(
    client: TestClient, project_root: Path, closure: str
) -> None:
    project = enroll(client, project_root)
    original_payload, operation_id = _proposal(project["id"], 0)
    original = client.post(f"/api/v1/projects/{project['id']}/proposals", json=original_payload)
    assert original.status_code == 201, original.text

    if closure == "apply":
        response = client.post(
            f"/api/v1/projects/{project['id']}/proposals/{original.json()['id']}/apply",
            json={"request_id": str(uuid4()), "selected_operation_ids": [operation_id]},
        )
        assert response.status_code == 200, response.text
        base_revision = response.json()["semantic_revision"]
    else:
        response = client.post(
            f"/api/v1/projects/{project['id']}/proposals/{original.json()['id']}/reject",
            json={"request_id": str(uuid4()), "reason": "Not needed"},
        )
        assert response.status_code == 200, response.text
        base_revision = 0

    repeated_payload, _new_operation_id = _proposal(project["id"], base_revision)
    repeated = client.post(f"/api/v1/projects/{project['id']}/proposals", json=repeated_payload)
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["id"] == original.json()["id"]
    assert repeated.json()["status"] == ("applied" if closure == "apply" else "rejected")
    listed = client.get(f"/api/v1/projects/{project['id']}/proposals").json()["proposals"]
    assert [proposal["id"] for proposal in listed] == [original.json()["id"]]
