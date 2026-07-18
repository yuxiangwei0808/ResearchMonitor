from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from research_monitor.api import create_app
from research_monitor.config import Settings
from research_monitor.database import Database


@pytest.fixture(autouse=True)
def enable_test_only_skill_source_override(monkeypatch):
    """Allow synthetic bundle injection only inside backend tests."""

    monkeypatch.setenv("RESEARCH_MONITOR_ENABLE_TEST_SKILL_SOURCE", "1")


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "PLAN.md").write_text("# Test research plan\n", encoding="utf-8")
    return root


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    home = tmp_path / "home"
    home.mkdir()
    return Settings(
        home=home,
        data_dir=home,
        config_dir=home,
        runtime_dir=home,
        database_path=home / "monitor.db",
        config_path=home / "config.json",
        runtime_descriptor=home / "server.json",
        lock_path=home / "app.lock",
        allowed_roots=(tmp_path.resolve(),),
        cli_token="test-cli-token",
    )


@pytest.fixture
def database(settings: Settings) -> Database:
    value = Database(settings.database_path)
    value.initialize()
    yield value
    value.engine.dispose()


@pytest.fixture
def client(settings: Settings, database: Database) -> TestClient:
    bootstrap_token = "test-browser-bootstrap"
    with TestClient(
        create_app(
            settings=settings,
            database=database,
            browser_bootstrap_token=bootstrap_token,
        )
    ) as value:
        bootstrap = value.get(
            f"/__bootstrap/{bootstrap_token}", follow_redirects=False
        )
        assert bootstrap.status_code == 303
        value.get("/")
        value.headers.update(
            {
                "Origin": "http://testserver",
                "X-CSRF-Token": value.cookies["research_monitor_csrf"],
            }
        )
        yield value


def enroll(client: TestClient, project_root: Path) -> dict:
    response = client.post("/api/v1/projects", json={"name": "Test research", "root_path": str(project_root)})
    assert response.status_code == 201, response.text
    return response.json()["project"]


def mutate(client: TestClient, project: dict, revision: int, operations: list[dict]) -> dict:
    from uuid import uuid4

    response = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": revision,
            "actor_type": "ui",
            "actor_label": "test",
            "operations": operations,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()
