from __future__ import annotations

from time import perf_counter
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import insert

from research_monitor.database import Database
from research_monitor.models import Artifact, ArtifactRoot, Pipeline, Project, ScanPolicy, Task


PROJECT_COUNT = 20
TASK_COUNT = 2_000
ARTIFACT_COUNT = 5_000
DONE_TASK_COUNT = 1_000
MAX_READ_SECONDS = 30.0


def _timed_get(client: TestClient, path: str, **kwargs):
    started = perf_counter()
    response = client.get(path, **kwargs)
    elapsed = perf_counter() - started
    assert response.status_code == 200, response.text
    assert elapsed < MAX_READ_SECONDS, f"{path} took {elapsed:.2f}s"
    return response, elapsed


def test_target_scale_reads_are_bounded_and_do_not_touch_project_files(
    client: TestClient,
    database: Database,
    tmp_path: Path,
) -> None:
    """Exercise the v1 target scale with central, metadata-only bulk fixtures."""
    roots: list[Path] = []
    projects: list[dict[str, str]] = []
    for index in range(PROJECT_COUNT):
        root = tmp_path / f"enrolled-project-{index:02d}"
        root.mkdir()
        roots.append(root)
        projects.append(
            {
                "id": str(uuid4()),
                "name": f"Research project {index:02d}",
                "root_path": str(root),
                "description": "",
                "research_goal": "",
                "success_criteria": "",
                "color": "#4f46e5",
            }
        )

    scaled_project = projects[0]
    scaled_project_id = scaled_project["id"]
    pipeline_id = str(uuid4())
    task_rows = []
    for index in range(TASK_COUNT):
        done = index < DONE_TASK_COUNT
        task_rows.append(
            {
                "id": str(uuid4()),
                "project_id": scaled_project_id,
                "pipeline_id": pipeline_id,
                "parent_id": None,
                "user_key": f"SCALE-{index:04d}",
                "title": f"Scale task searchablemarker {index:04d}",
                "description": "Bounded target-scale task metadata",
                "status": "done" if done else "planned",
                "outcome": "successful" if done else "not_applicable",
                "priority": "recommended",
                "labels_json": '["scale"]',
                "order_index": float(index),
                "completion_summary": "Completed for scale fixture" if done else "",
                "completion_actor": "scale-test" if done else "",
                "completion_source": "direct-test-fixture" if done else "",
                "completion_provenance": "manual",
                "child_flow_mode": "freeform",
            }
        )
    artifact_rows = [
        {
            "id": str(uuid4()),
            "project_id": scaled_project_id,
            "root_id": None,
            "locator_type": "url",
            "locator": f"https://example.invalid/runs/{index:05d}",
            "provider": "metadata-only",
            "label": f"Scale artifact artifactmarker {index:05d}",
            "notes": "External locator metadata; no artifact body is stored or fetched.",
        }
        for index in range(ARTIFACT_COUNT)
    ]

    with database.write_session() as session:
        session.execute(insert(Project), projects)
        session.execute(
            insert(ScanPolicy),
            [{"project_id": project["id"]} for project in projects],
        )
        session.execute(
            insert(ArtifactRoot),
            [
                {
                    "id": str(uuid4()),
                    "project_id": project["id"],
                    "alias": "Project root",
                    "root_path": project["root_path"],
                    "is_project_root": True,
                }
                for project in projects
            ],
        )
        session.execute(
            insert(Pipeline),
            [
                {
                    "id": pipeline_id,
                    "project_id": scaled_project_id,
                    "title": "Scale pipeline",
                    "description": "",
                    "flow_mode": "freeform",
                    "order_index": 0.0,
                }
            ],
        )
        session.execute(insert(Task), task_rows)
        session.execute(insert(Artifact), artifact_rows)

    portfolio_response, portfolio_elapsed = _timed_get(
        client,
        "/api/v1/projects",
        params={"include_archived": True},
    )
    portfolio = portfolio_response.json()["projects"]
    assert len(portfolio) == PROJECT_COUNT
    scaled_card = next(item for item in portfolio if item["id"] == scaled_project_id)
    assert scaled_card["progress"]["leaf_total"] == TASK_COUNT
    assert scaled_card["progress"]["leaf_done"] == DONE_TASK_COUNT
    assert scaled_card["progress"]["by_status"] == {
        "done": DONE_TASK_COUNT,
        "planned": TASK_COUNT - DONE_TASK_COUNT,
    }

    snapshot_response, snapshot_elapsed = _timed_get(
        client,
        f"/api/v1/projects/{scaled_project_id}/snapshot",
    )
    snapshot = snapshot_response.json()
    assert len(snapshot["tasks"]) == TASK_COUNT
    assert len(snapshot["artifacts"]) == ARTIFACT_COUNT
    assert snapshot["progress"]["leaf_total"] == TASK_COUNT
    assert snapshot["progress"]["leaf_done"] == DONE_TASK_COUNT
    assert snapshot["progress"]["ready"] == TASK_COUNT - DONE_TASK_COUNT
    assert all(artifact["kind"] == "url" for artifact in snapshot["artifacts"])
    assert all(artifact["available"] is None for artifact in snapshot["artifacts"])
    assert all(
        "body" not in artifact and "content" not in artifact
        for artifact in snapshot["artifacts"]
    )
    # The full metadata snapshot remains finite without embedding artifact bodies.
    assert len(snapshot_response.content) < 12_000_000

    overview_response, overview_elapsed = _timed_get(
        client,
        f"/api/v1/projects/{scaled_project_id}/snapshot",
        params={"sections": "progress,pipelines,tasks"},
    )
    overview = overview_response.json()
    assert len(overview["tasks"]) == TASK_COUNT
    assert overview["artifacts"] == []
    assert overview["task_artifacts"] == []
    assert overview["progress"]["leaf_total"] == TASK_COUNT
    assert len(overview_response.content) < 4_000_000

    settings_response, settings_elapsed = _timed_get(
        client,
        f"/api/v1/projects/{scaled_project_id}/snapshot",
        params={"sections": "scan_policy,artifact_roots"},
    )
    settings_snapshot = settings_response.json()
    assert settings_snapshot["tasks"] == []
    assert settings_snapshot["artifacts"] == []
    assert len(settings_snapshot["artifact_roots"]) == 1
    assert len(settings_response.content) < 100_000

    invalid_section = client.get(
        f"/api/v1/projects/{scaled_project_id}/snapshot",
        params={"sections": "tasks,not-a-section"},
    )
    assert invalid_section.status_code == 422
    assert invalid_section.json()["detail"]["code"] == "invalid_snapshot_section"

    task_search_response, task_search_elapsed = _timed_get(
        client,
        f"/api/v1/projects/{scaled_project_id}/search",
        params=[
            ("q", "searchablemarker"),
            ("entity_type", "task"),
            ("limit", "37"),
        ],
    )
    task_search = task_search_response.json()
    assert task_search["total"] == TASK_COUNT
    assert task_search["count"] == 37
    assert len(task_search["results"]) == 37
    assert all(item["entity_type"] == "task" for item in task_search["results"])

    artifact_search_response, artifact_search_elapsed = _timed_get(
        client,
        f"/api/v1/projects/{scaled_project_id}/search",
        params=[
            ("q", "artifactmarker"),
            ("entity_type", "artifact"),
            ("artifact_type", "url"),
            ("limit", "29"),
        ],
    )
    artifact_search = artifact_search_response.json()
    assert artifact_search["total"] == ARTIFACT_COUNT
    assert artifact_search["count"] == 29
    assert len(artifact_search["results"]) == 29
    assert all(
        item["entity_type"] == "artifact" and item["artifact_type"] == "url"
        for item in artifact_search["results"]
    )
    assert len(artifact_search_response.content) < 100_000

    # Keep the measured values visible in assertion reports without brittle
    # micro-benchmarks; each individual bound is deliberately generous.
    assert max(
        portfolio_elapsed,
        snapshot_elapsed,
        overview_elapsed,
        settings_elapsed,
        task_search_elapsed,
        artifact_search_elapsed,
    ) < MAX_READ_SECONDS
    assert all(root.is_dir() and not any(root.iterdir()) for root in roots)
