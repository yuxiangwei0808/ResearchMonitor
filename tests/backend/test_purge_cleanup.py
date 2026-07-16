from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from research_monitor.lifecycle import purge_project
from research_monitor.models import IdempotencyRecord, OutboxEvent

from .conftest import enroll, mutate
from .test_api import op


def test_permanent_purge_removes_project_scoped_coordination_rows(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    mutate(client, project, 0, [op("project.trash", {}, project["id"])])

    with database.session() as session:
        assert session.query(IdempotencyRecord).filter_by(project_id=project["id"]).count() > 0
        assert session.query(OutboxEvent).filter_by(project_id=project["id"]).count() > 0

    purge_project(database, project["id"], confirm=project["id"])

    with database.session() as session:
        assert session.query(IdempotencyRecord).filter_by(project_id=project["id"]).count() == 0
        assert session.query(OutboxEvent).filter_by(project_id=project["id"]).count() == 0
