from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from research_monitor.preview import SafeOpenError, open_regular_beneath
from research_monitor.proposals import AppService

from .conftest import enroll, mutate
from .test_api import op


def test_sensitive_ancestor_and_custom_policy_block_preview(client: TestClient, project_root: Path) -> None:
    secret_dir = project_root / "secret"; secret_dir.mkdir(); (secret_dir / "result.txt").write_text("private", encoding="utf-8")
    custom_dir = project_root / "embargoed"; custom_dir.mkdir(); (custom_dir / "report.txt").write_text("private", encoding="utf-8")
    project = enroll(client, project_root); snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json(); root_id = snapshot["artifact_roots"][0]["id"]
    first_id, second_id = str(uuid4()), str(uuid4())
    changed = mutate(client, project, 0, [
        op("scan_policy.update", {"sensitive_patterns": ["secret", "embargoed"]}, project["id"], snapshot["scan_policy"]["version"]),
        op("artifact.create", {"id": first_id, "kind": "local", "artifact_root_id": root_id, "locator": "secret/result.txt", "label": "secret"}),
        op("artifact.create", {"id": second_id, "kind": "local", "artifact_root_id": root_id, "locator": "embargoed/report.txt", "label": "custom"}),
    ])
    assert changed["semantic_revision"] == 1
    assert client.get(f"/api/v1/artifacts/{first_id}/preview").status_code == 415
    assert client.get(f"/api/v1/artifacts/{second_id}/preview").status_code == 415


def test_replaced_symlink_is_revalidated_at_preview(client: TestClient, project_root: Path, tmp_path: Path) -> None:
    local = project_root / "result.txt"; local.write_text("safe", encoding="utf-8")
    outside = tmp_path / "outside.txt"; outside.write_text("TOP_SECRET_PAYLOAD_7391", encoding="utf-8")
    project = enroll(client, project_root); root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"][0]["id"]; artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {"id": artifact_id, "kind": "local", "artifact_root_id": root_id, "locator": "result.txt", "label": "result"})])
    local.unlink(); local.symlink_to(outside)
    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")
    assert response.status_code == 403
    assert b"TOP_SECRET_PAYLOAD_7391" not in response.content


def test_duplicate_external_locator_is_rejected(client: TestClient, project_root: Path) -> None:
    project = enroll(client, project_root); url = "https://wandb.ai/example/project/runs/abc"
    first = mutate(client, project, 0, [op("artifact.create", {"kind": "url", "locator": url, "label": "Run", "provider": "W&B"})])
    duplicate = client.post(f"/api/v1/projects/{project['id']}/mutations", json={"api_version": "1", "schema_version": "1", "request_id": str(uuid4()), "project_id": project["id"], "base_semantic_revision": first["semantic_revision"], "actor_type": "ui", "operations": [op("artifact.create", {"kind": "url", "locator": url, "label": "Same run", "provider": "W&B"})]})
    assert duplicate.status_code == 409


def test_common_key_paths_are_never_previewed(client: TestClient, project_root: Path) -> None:
    key_dir = project_root / "keys"
    key_dir.mkdir()
    (key_dir / "api_key.txt").write_text("DO_NOT_RENDER", encoding="utf-8")
    project = enroll(client, project_root)
    root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {
        "id": artifact_id, "kind": "local", "artifact_root_id": root_id,
        "locator": "keys/api_key.txt", "label": "key",
    })])

    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")
    assert response.status_code == 415
    assert b"DO_NOT_RENDER" not in response.content


def test_deleted_artifact_locator_cannot_be_recreated(
    client: TestClient, project_root: Path
) -> None:
    (project_root / "result.txt").write_text("result", encoding="utf-8")
    project = enroll(client, project_root)
    root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"][0]["id"]
    pipeline_id, task_id, artifact_id = [str(uuid4()) for _ in range(3)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P"}),
        op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "T"}),
        op("artifact.create", {"id": artifact_id, "kind": "local", "artifact_root_id": root_id, "locator": "result.txt", "label": "Old"}),
    ])
    deleted = mutate(client, project, created["semantic_revision"], [op("artifact.delete", {}, artifact_id, 1)])
    collision = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": deleted["semantic_revision"],
            "actor_type": "ui",
            "operations": [
                op(
                    "artifact.create",
                    {
                        "id": str(uuid4()),
                        "kind": "local",
                        "artifact_root_id": root_id,
                        "locator": "result.txt",
                        "label": "Replacement",
                    },
                )
            ],
        },
    )

    assert collision.status_code == 409, collision.text
    assert collision.json()["detail"]["code"] == "entity_deleted"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == deleted["semantic_revision"]
    assert len(snapshot["artifacts"]) == 1
    assert snapshot["artifacts"][0]["id"] == artifact_id
    assert snapshot["artifacts"][0]["label"] == "Old"
    assert snapshot["artifacts"][0]["deleted_at"] is not None
    assert snapshot["task_artifacts"] == []


def test_deleted_artifact_locator_cannot_be_recreated_and_linked_in_one_mutation(
    client: TestClient, project_root: Path
) -> None:
    (project_root / "same-batch.txt").write_text("result", encoding="utf-8")
    project = enroll(client, project_root)
    root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"][0]["id"]
    pipeline_id, task_id, old_artifact_id, proposed_artifact_id = [str(uuid4()) for _ in range(4)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P"}),
        op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "T"}),
        op("artifact.create", {
            "id": old_artifact_id, "kind": "local", "artifact_root_id": root_id,
            "locator": "same-batch.txt", "label": "Old",
        }),
    ])
    deleted = mutate(client, project, created["semantic_revision"], [
        op("artifact.delete", {}, old_artifact_id, 1),
    ])
    collision = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": deleted["semantic_revision"],
            "actor_type": "ui",
            "operations": [
                op("artifact.create", {
                    "id": proposed_artifact_id, "kind": "local", "artifact_root_id": root_id,
                    "locator": "same-batch.txt", "label": "Replacement",
                }),
                op("task_artifact.link", {
                    "task_id": task_id, "artifact_id": proposed_artifact_id, "role": "evidence",
                }),
            ],
        },
    )

    assert collision.status_code == 409, collision.text
    assert collision.json()["detail"]["code"] == "entity_deleted"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == deleted["semantic_revision"]
    assert len(snapshot["artifacts"]) == 1
    assert snapshot["artifacts"][0]["id"] == old_artifact_id
    assert snapshot["artifacts"][0]["label"] == "Old"
    assert snapshot["artifacts"][0]["deleted_at"] is not None
    assert snapshot["task_artifacts"] == []


def test_preview_rejects_in_root_symlink_alias(
    client: TestClient, project_root: Path
) -> None:
    private = project_root / "private"
    private.mkdir()
    (private / "report.txt").write_text("do-not-preview", encoding="utf-8")
    (project_root / "public.txt").symlink_to(private / "report.txt")

    project = enroll(client, project_root)
    root_id = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {
        "id": artifact_id,
        "kind": "local",
        "artifact_root_id": root_id,
        "locator": "public.txt",
        "label": "Alias",
    })])

    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")
    assert response.status_code == 403
    assert b"do-not-preview" not in response.content


def test_opened_artifact_streams_the_same_validated_file_descriptor(
    project_root: Path, tmp_path: Path
) -> None:
    safe = project_root / "result.txt"
    safe.write_bytes(b"validated-content")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"replacement-content")

    opened = open_regular_beneath(project_root, "result.txt")
    safe.unlink()
    safe.symlink_to(outside)

    assert opened.read_all() == b"validated-content"


def test_markdown_descriptor_read_is_capped_at_validated_size(
    project_root: Path,
) -> None:
    safe = project_root / "growing.md"
    initial = b"# validated snapshot\n"
    safe.write_bytes(initial)
    opened = open_regular_beneath(project_root, safe.name)
    with safe.open("ab") as handle:
        handle.write(b"x" * (1024 * 1024))

    assert opened.size_bytes == len(initial)
    assert opened.read_all() == initial


def test_stream_descriptor_ignores_continuous_growth_after_open(
    project_root: Path,
) -> None:
    safe = project_root / "growing.log"
    initial = b"0123456789"
    safe.write_bytes(initial)
    opened = open_regular_beneath(project_root, safe.name)
    chunks = opened.iter_bytes(chunk_size=4)
    first = next(chunks)
    with safe.open("ab") as handle:
        handle.write(b"new-data" * (256 * 1024))

    assert first + b"".join(chunks) == initial


def test_descriptor_read_detects_shortening_after_validation(
    project_root: Path,
) -> None:
    safe = project_root / "shortened.txt"
    safe.write_bytes(b"validated-content")
    opened = open_regular_beneath(project_root, safe.name)
    safe.write_bytes(b"x")

    with pytest.raises(SafeOpenError, match="shortened after preview validation"):
        opened.read_all()


def test_stream_descriptor_detects_shortening_after_validation(
    project_root: Path,
) -> None:
    safe = project_root / "shortened.log"
    safe.write_bytes(b"validated-content")
    opened = open_regular_beneath(project_root, safe.name)
    safe.write_bytes(b"x")

    with pytest.raises(SafeOpenError, match="shortened after preview validation"):
        b"".join(opened.iter_bytes(chunk_size=4))

def test_unstarted_stream_iterator_can_release_descriptor(
    project_root: Path,
) -> None:
    safe = project_root / "unstarted.log"
    safe.write_bytes(b"validated-content")
    opened = open_regular_beneath(project_root, safe.name)
    descriptor = opened.fd
    iterator = opened.iter_bytes()

    iterator.close()
    assert opened.fd == -1
    with pytest.raises(OSError):
        os.fstat(descriptor)



def test_invalid_stream_chunk_size_closes_descriptor(
    project_root: Path,
) -> None:
    safe = project_root / "invalid-chunk.log"
    safe.write_bytes(b"validated-content")
    opened = open_regular_beneath(project_root, safe.name)
    descriptor = opened.fd

    with pytest.raises(ValueError, match="chunk_size must be positive"):
        opened.iter_bytes(chunk_size=0)

    assert opened.fd == -1
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_preview_response_is_capped_when_file_grows_after_validation(
    client: TestClient,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = project_root / "growing-response.txt"
    initial = b"validated-response"
    safe.write_bytes(initial)
    project = enroll(client, project_root)
    root_id = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {
        "id": artifact_id,
        "kind": "local",
        "artifact_root_id": root_id,
        "locator": safe.name,
        "label": "Growing response",
    })])
    original = AppService.artifact_preview

    def append_after_open(
        service: AppService,
        session: object,
        requested_artifact_id: str,
    ):
        opened = original(service, session, requested_artifact_id)
        with safe.open("ab") as handle:
            handle.write(b"x" * (1024 * 1024))
        return opened

    monkeypatch.setattr(AppService, "artifact_preview", append_after_open)
    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")

    assert response.status_code == 200
    assert response.content == initial
    assert response.headers["content-length"] == str(len(initial))


def test_preview_shortening_returns_structured_conflict(
    client: TestClient,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe = project_root / "shortened-response.txt"
    safe.write_bytes(b"validated-response")
    project = enroll(client, project_root)
    root_id = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {
        "id": artifact_id,
        "kind": "local",
        "artifact_root_id": root_id,
        "locator": safe.name,
        "label": "Shortened response",
    })])
    original = AppService.artifact_preview

    def truncate_after_open(
        service: AppService,
        session: object,
        requested_artifact_id: str,
    ):
        opened = original(service, session, requested_artifact_id)
        safe.write_bytes(b"x")
        return opened

    monkeypatch.setattr(AppService, "artifact_preview", truncate_after_open)
    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "artifact_changed"


def test_unicode_preview_filename_uses_rfc5987_header(
    client: TestClient,
    project_root: Path,
) -> None:
    safe = project_root / "报告.txt"
    safe.write_text("validated", encoding="utf-8")
    project = enroll(client, project_root)
    root_id = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {
        "id": artifact_id,
        "kind": "local",
        "artifact_root_id": root_id,
        "locator": safe.name,
        "label": "Unicode report",
    })])

    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert 'filename="__.txt"' in disposition
    assert "filename*=UTF-8''%E6%8A%A5%E5%91%8A.txt" in disposition


def test_markdown_preview_is_bounded_html_without_active_content(
    client: TestClient, project_root: Path
) -> None:
    (project_root / "README.md").write_text(
        "# Safe\n\n<script>alert('unsafe')</script>\n\n"
        "![pixel](https://evil.example/pixel.png)\n\n**bold**",
        encoding="utf-8",
    )
    project = enroll(client, project_root)
    root_id = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    mutate(client, project, 0, [op("artifact.create", {
        "id": artifact_id,
        "kind": "local",
        "artifact_root_id": root_id,
        "locator": "README.md",
        "label": "Readme",
    })])

    metadata = client.get(f"/api/v1/artifacts/{artifact_id}/metadata")
    assert metadata.status_code == 200, metadata.text
    assert metadata.json()["preview_mode"] == "markdown"

    response = client.get(f"/api/v1/artifacts/{artifact_id}/preview")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Safe</h1>" in response.text
    assert "&lt;script&gt;" in response.text
    assert "<strong>bold</strong>" in response.text
    assert "<script>" not in response.text
    assert "<img" not in response.text
    assert "sandbox" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_deleting_artifact_root_with_tombstoned_history_is_controlled_conflict(
    client: TestClient, project_root: Path, tmp_path: Path
) -> None:
    extra_root = tmp_path / "approved"
    extra_root.mkdir()
    (extra_root / "result.txt").write_text("result", encoding="utf-8")

    project = enroll(client, project_root)
    root_id, artifact_id = str(uuid4()), str(uuid4())
    created = mutate(client, project, 0, [
        op("artifact_root.create", {
            "id": root_id,
            "canonical_path": str(extra_root),
            "name": "approved",
        }),
        op("artifact.create", {
            "id": artifact_id,
            "kind": "local",
            "artifact_root_id": root_id,
            "locator": "result.txt",
            "label": "Result",
        }),
    ])
    deleted = mutate(client, project, created["semantic_revision"], [
        op("artifact.delete", {}, artifact_id, 1),
    ])

    response = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": deleted["semantic_revision"],
            "actor_type": "ui",
            "operations": [op("artifact_root.delete", {}, root_id, 1)],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "artifact_root_history_retained"
