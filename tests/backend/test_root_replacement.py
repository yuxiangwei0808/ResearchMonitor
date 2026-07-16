from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from .conftest import enroll, mutate
from .test_api import op


def test_approved_root_replaced_by_symlink_cannot_expand_access(client: TestClient, project_root: Path, tmp_path: Path) -> None:
    (project_root / "result.txt").write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside-root"; outside.mkdir(); (outside / "result.txt").write_text("TOP_SECRET_REPLACEMENT", encoding="utf-8")
    project = enroll(client, project_root); root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"][0]["id"]; artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {"id": artifact_id, "kind": "local", "artifact_root_id": root_id, "locator": "result.txt", "label": "Result"})])
    moved = project_root.with_name("original-moved"); project_root.rename(moved); project_root.symlink_to(outside, target_is_directory=True)
    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")
    assert response.status_code == 403
    assert b"TOP_SECRET_REPLACEMENT" not in response.content
    listed = client.get("/api/v1/projects").json()["projects"]
    assert listed[0]["unavailable"] is True


def test_project_relink_immediately_stores_missing_and_escaping_artifact_warnings(
    client: TestClient, project_root: Path, tmp_path: Path
) -> None:
    safe = project_root / "redirected.txt"
    safe.write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("NEVER_READ_THIS", encoding="utf-8")
    replacement = tmp_path / "replacement-root"
    replacement.mkdir()
    (replacement / "redirected.txt").symlink_to(outside)

    project = enroll(client, project_root)
    root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"][0]["id"]
    missing_id, escaping_id = str(uuid4()), str(uuid4())
    created = mutate(client, project, 0, [
        op("artifact.create", {
            "id": missing_id, "kind": "local", "artifact_root_id": root_id,
            "locator": "missing.txt", "label": "Missing",
        }),
        op("artifact.create", {
            "id": escaping_id, "kind": "local", "artifact_root_id": root_id,
            "locator": "redirected.txt", "label": "Redirected",
        }),
    ])
    relinked = mutate(client, project, created["semantic_revision"], [
        op("project.relink", {"root_path": str(replacement)}, project["id"]),
    ])

    warnings = relinked["results"][0]["value"]["artifact_warnings"]
    by_id = {item["artifact_id"]: item for item in warnings}
    assert by_id[missing_id]["code"] == "artifact_missing"
    assert by_id[escaping_id]["code"] == "artifact_escape"

    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    artifacts = {item["id"]: item for item in snapshot["artifacts"]}
    assert artifacts[missing_id]["available"] is False
    assert artifacts[escaping_id]["available"] is False
    assert artifacts[missing_id]["validation_warning"].startswith("artifact_missing:")
    assert artifacts[escaping_id]["validation_warning"].startswith("artifact_escape:")

    preview = client.get(f"/api/v1/artifacts/{escaping_id}/preview")
    assert preview.status_code == 403
    assert b"NEVER_READ_THIS" not in preview.content
