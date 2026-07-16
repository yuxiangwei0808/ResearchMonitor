from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from typer.testing import CliRunner

import research_monitor.cli as cli_module
from research_monitor.cli import app
from research_monitor.database import reset_database_singleton
from research_monitor.mutations import operation_integrity_error

from .conftest import enroll


runner = CliRunner()


def _mutation(project: dict, revision: int, operation: dict) -> dict:
    return {
        "api_version": "1",
        "schema_version": "1",
        "request_id": str(uuid4()),
        "project_id": project["id"],
        "base_semantic_revision": revision,
        "actor_type": "ui",
        "operations": [operation],
    }


def test_offline_cli_incompatible_database_has_common_envelope_and_exit_5(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    connection = sqlite3.connect(home / "monitor.db")
    connection.execute("CREATE TABLE schema_versions (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_versions(version) VALUES (999)")
    connection.commit()
    connection.close()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()
    try:
        response = runner.invoke(app, ["project", "list", "--json"])
        assert response.exit_code == 5, response.output
        body = json.loads(response.output)
        assert set(body) == {"api_version", "schema_version", "request_id", "error"}
        assert body["error"] == {
            "code": "schema_incompatible",
            "message": "Database schema 999 is incompatible with 1",
            "details": {"found": 999, "expected": 1},
        }
    finally:
        reset_database_singleton()


@pytest.mark.parametrize(
    "version_contract",
    [{"api_version": "999", "schema_version": "1"}, []],
    ids=["version-mismatch", "malformed-contract"],
)
def test_online_cli_incompatible_api_has_common_envelope_and_exit_5(
    tmp_path: Path, monkeypatch, version_contract
) -> None:
    home = tmp_path / "monitor-home"
    home.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))

    class BusyLock:
        def __init__(self, _path: Path):
            pass

        def acquire(self) -> bool:
            return False

        def release(self) -> None:
            pass

    class IncompatibleClient:
        def request(self, method: str, path: str, **_kwargs):
            assert (method, path) == ("GET", "/api/v1/version")
            return version_contract

    monkeypatch.setattr(cli_module, "ApplicationLock", BusyLock)
    monkeypatch.setattr(
        cli_module.RuntimeClient,
        "discover",
        classmethod(lambda _cls, _settings: IncompatibleClient()),
    )
    response = runner.invoke(app, ["project", "list", "--json"])
    assert response.exit_code == 5, response.output
    body = json.loads(response.output)
    assert set(body) == {"api_version", "schema_version", "request_id", "error"}
    assert body["error"]["code"] == "api_incompatible"


def test_malformed_human_operation_is_structured_422_and_atomic(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    response = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json=_mutation(
            project,
            0,
            {
                "id": str(uuid4()),
                "type": "pipeline.create",
                "data": {"id": str(uuid4()), "title": "Bad order", "position": "first"},
            },
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert response.json()["detail"]["details"]
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 0
    assert snapshot["pipelines"] == []


def test_malformed_agent_operation_is_structured_422(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    operation_id = str(uuid4())
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": 0,
            "summary": "Malformed nested task",
            "operations": [
                {
                    "id": operation_id,
                    "type": "task.create",
                    "data": {
                        "id": str(uuid4()),
                        "pipeline_id": [],
                        "title": "Task",
                    },
                    "rationale": "Source says so",
                    "confidence": 0.8,
                    "evidence": ["PLAN.md"],
                }
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert response.json()["detail"]["details"]


def test_duplicate_human_entity_identity_is_structured_422(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id = str(uuid4())
    operation = {
        "id": str(uuid4()),
        "type": "pipeline.create",
        "data": {"id": pipeline_id, "title": "First"},
    }
    first = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json=_mutation(project, 0, operation),
    )
    assert first.status_code == 200, first.text
    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json=_mutation(
            project,
            1,
            {
                "id": str(uuid4()),
                "type": "pipeline.create",
                "data": {"id": pipeline_id, "title": "Duplicate"},
            },
        ),
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "operation_integrity_error"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["semantic_revision"] == 1
    assert [row["title"] for row in snapshot["pipelines"]] == ["First"]


def test_duplicate_agent_operation_identity_is_structured_422(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    operation_id = str(uuid4())

    def proposal(title: str) -> dict:
        return {
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": 0,
            "summary": title,
            "operations": [
                {
                    "id": operation_id,
                    "type": "pipeline.create",
                    "data": {"id": str(uuid4()), "title": title},
                    "rationale": "Document heading",
                    "confidence": 0.9,
                    "evidence": ["PLAN.md"],
                }
            ],
        }

    first = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json=proposal("First proposal"),
    )
    assert first.status_code == 201, first.text
    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json=proposal("Second proposal"),
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["code"] == "operation_integrity_error"


def test_internal_integrity_errors_are_not_misreported_as_client_input() -> None:
    exception = IntegrityError(
        "INSERT INTO audit_events ...",
        {},
        sqlite3.IntegrityError("UNIQUE constraint failed: audit_events.sequence"),
    )
    assert operation_integrity_error(exception) is None
