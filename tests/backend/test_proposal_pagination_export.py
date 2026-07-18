from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from research_monitor.models import (
    AgentIntent,
    ArtifactRoot,
    JournalEntry,
    Proposal,
    ProposalOperation,
    SourceReference,
    TaskSourceReference,
)
from research_monitor.serializers import canonical_json
from .conftest import enroll, mutate


def _op(operation_type: str, data: dict, *, entity_id: str | None = None) -> dict:
    value = {"id": str(uuid4()), "type": operation_type, "data": data}
    if entity_id is not None:
        value["entity_id"] = entity_id
    return value


def _seed_proposal_history(database, project_id: str) -> list[str]:
    created = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    statuses = ["applied", "draft", "rejected", "draft", "no_changes"]
    proposal_ids: list[str] = []
    with database.session() as session:
        for index, status in enumerate(statuses):
            proposal_id = str(uuid4())
            proposal_ids.append(proposal_id)
            proposal = Proposal(
                id=proposal_id,
                project_id=project_id,
                request_id=proposal_id,
                base_semantic_revision=0,
                summary=f"Proposal {index}",
                rationale=f"Rationale {index}",
                status=status,
                fingerprint=f"fingerprint-{index}",
                proposal_contract_version="2" if index >= 3 else "1",
                workflow_mode="record_update" if index in {1, 3} else "legacy_custom",
                scope_type="task" if index in {1, 3} else "project",
                scope_id=str(uuid4()) if index in {1, 3} else None,
                result_kind="no_changes" if status == "no_changes" else "changes",
                no_change_reason="up_to_date" if status == "no_changes" else "",
                scan_summary_json=(
                    canonical_json({"files_read": 2, "text_bytes_read": 40})
                    if status == "no_changes"
                    else "{}"
                ),
                top_level_evidence_json=canonical_json(
                    [{"kind": "user_instruction", "summary": "Check"}]
                ),
                top_level_source_references_json="[]",
                fingerprint_version=2 if index >= 3 else 1,
                created_at=created + timedelta(minutes=index),
                closed_at=(
                    None
                    if status == "draft"
                    else created + timedelta(minutes=index, seconds=30)
                ),
            )
            session.add(proposal)
            if index in {1, 3}:
                operation_id = str(uuid4())
                operation = {
                    "id": operation_id,
                    "type": "task.update",
                    "entity_id": str(uuid4()),
                    "data": {"status": "done", "completion_summary": "Finished"},
                    "basis": "inference" if index == 1 else "user_instruction",
                    "rationale": "Recorded update",
                    "confidence": 0.7,
                    "evidence": [],
                    "source_references": [],
                    "prerequisite_operation_ids": [],
                }
                session.add(
                    ProposalOperation(
                        id=operation_id,
                        proposal_id=proposal_id,
                        operation_type="task.update",
                        operation_json=canonical_json(operation),
                        rationale="Recorded update",
                        confidence=0.7,
                        evidence_json="[]",
                        source_references_json="[]",
                        basis=operation["basis"],
                        disposition="pending",
                    )
                )
    return proposal_ids


def test_proposal_summary_cursor_filters_counts_and_lazy_detail(
    client: TestClient,
    project_root: Path,
    database,
) -> None:
    project = enroll(client, project_root)
    proposal_ids = _seed_proposal_history(database, project["id"])

    legacy = client.get(f"/api/v1/projects/{project['id']}/proposals")
    assert legacy.status_code == 200
    assert len(legacy.json()["proposals"]) == 5
    assert legacy.json()["proposals"][1]["operations"][0]["type"] == "task.update"

    first = client.get(
        f"/api/v1/projects/{project['id']}/proposals",
        params={"summary": "true", "limit": 2},
    )
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert [item["id"] for item in first_page["proposals"]] == [
        proposal_ids[4],
        proposal_ids[3],
    ]
    assert first_page["total"] == 5
    assert first_page["draft_count"] == 2
    assert first_page["closed_count"] == 3
    assert first_page["status_counts"] == {
        "applied": 1,
        "draft": 2,
        "no_changes": 1,
        "rejected": 1,
    }
    assert first_page["has_more"] is True
    assert first_page["next_cursor"]
    draft_summary = first_page["proposals"][1]
    assert draft_summary["operations"] == []
    assert draft_summary["operation_count"] == 1
    assert draft_summary["risk_counts"] == {"normal": 0, "high": 1}
    assert draft_summary["basis_counts"] == {"user_instruction": 1}
    assert draft_summary["detail_loaded"] is False

    second = client.get(
        f"/api/v1/projects/{project['id']}/proposals",
        params={
            "summary": "true",
            "limit": 2,
            "cursor": first_page["next_cursor"],
        },
    )
    assert second.status_code == 200, second.text
    assert [item["id"] for item in second.json()["proposals"]] == [
        proposal_ids[2],
        proposal_ids[1],
    ]

    open_drafts = client.get(
        f"/api/v1/projects/{project['id']}/proposals",
        params={"summary": "true", "status": "open", "limit": 100},
    ).json()
    assert [item["id"] for item in open_drafts["proposals"]] == [
        proposal_ids[3],
        proposal_ids[1],
    ]
    assert open_drafts["total"] == 2
    assert open_drafts["draft_count"] == 2

    filtered = client.get(
        f"/api/v1/projects/{project['id']}/proposals",
        params={
            "summary": "true",
            "workflow_mode": "record_update",
            "scope_type": "task",
            "limit": 100,
        },
    ).json()
    assert filtered["total"] == 2
    assert filtered["draft_count"] == 2
    assert filtered["workflow_mode_counts"]["record_update"] == 2

    detail = client.get(f"/api/v1/proposals/{proposal_ids[3]}")
    assert detail.status_code == 200
    assert detail.json()["operations"][0]["type"] == "task.update"

    full_page = client.get(
        f"/api/v1/projects/{project['id']}/proposals",
        params={"summary": "false", "status": "open", "limit": 1},
    ).json()
    assert full_page["summary"] is False
    assert full_page["proposals"][0]["operations"][0]["type"] == "task.update"

    for malformed_cursor in ("not-a-cursor", "A", "☃"):
        invalid = client.get(
            f"/api/v1/projects/{project['id']}/proposals",
            params={"summary": "true", "cursor": malformed_cursor},
        )
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "invalid_proposal_cursor"
    mismatched = client.get(
        f"/api/v1/projects/{project['id']}/proposals",
        params={
            "summary": "true",
            "status": "closed",
            "cursor": first_page["next_cursor"],
        },
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["detail"]["code"] == "proposal_cursor_mismatch"


def test_v02_export_is_portable_complete_and_excludes_agent_intents(
    client: TestClient,
    project_root: Path,
    database,
    tmp_path: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id, journal_id = [str(uuid4()) for _ in range(3)]
    mutate(
        client,
        project,
        0,
        [
            _op("pipeline.create", {"id": pipeline_id, "title": "Analysis"}),
            _op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Run"}),
            _op(
                "journal.create",
                {"id": journal_id, "task_id": task_id, "content": "Observed progress"},
            ),
        ],
    )
    shared_root = tmp_path / "shared-results"
    shared_root.mkdir()
    mutate(
        client,
        project,
        1,
        [_op("artifact_root.create", {"name": "Shared", "canonical_path": str(shared_root)})],
    )
    roots = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()["artifact_roots"]
    shared_root_id = next(root["id"] for root in roots if root["name"] == "Shared")
    mutate(
        client,
        project,
        2,
        [
            _op(
                "scan_policy.update",
                {
                    "readable_source_root_ids": [shared_root_id],
                    "max_files_per_scan": 37,
                    "max_total_text_bytes": 2 * 1024 * 1024,
                },
            ),
            _op(
                "planning_profile.update",
                {
                    "task_granularity": "detailed",
                    "max_nesting_depth": 4,
                    "protected_task_ids": [task_id],
                    "terminology_notes": "Use domain terminology",
                },
            ),
        ],
    )

    source_id = str(uuid4())
    association_id = str(uuid4())
    hidden_instruction = "agent-intent-instructions-must-not-export"
    with database.session() as session:
        journal = session.get(JournalEntry, journal_id)
        assert journal is not None
        journal.origin_key = "source:accepted-fingerprint"
        source = SourceReference(
            id=source_id,
            project_id=project["id"],
            task_id=task_id,
            source_root_id=shared_root_id,
            source_path="notes/progress.md",
            anchor="Experiment complete",
            opaque_key="EXP-1",
            fingerprint="f" * 64,
        )
        session.add(source)
        session.add(
            TaskSourceReference(
                id=association_id,
                project_id=project["id"],
                task_id=task_id,
                source_reference_id=source_id,
            )
        )
        session.add(
            AgentIntent(
                id=str(uuid4()),
                proposal_request_id=str(uuid4()),
                project_id=project["id"],
                issued_semantic_revision=3,
                planning_profile_version=2,
                workflow_mode="record_update",
                scope_type="task",
                scope_id=task_id,
                instructions=hidden_instruction,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )

    first = client.get(f"/api/v1/projects/{project['id']}/export")
    second = client.get(f"/api/v1/projects/{project['id']}/export")
    assert first.status_code == second.status_code == 200
    assert canonical_json(first.json()) == canonical_json(second.json())
    exported = first.json()
    assert exported["schema_version"] == "1"
    assert exported["export_contract_version"] == "2"
    monitor = exported["project"]
    serialized = canonical_json(exported)
    assert hidden_instruction not in serialized
    assert str(project_root) not in serialized
    assert str(shared_root) not in serialized
    assert "agent_intents" not in monitor

    shared_alias = next(
        root["alias"] for root in monitor["artifact_roots"] if root["name"] == "Shared"
    )
    assert monitor["scan_policy"]["readable_source_root_aliases"] == [shared_alias]
    assert "readable_source_root_ids" not in monitor["scan_policy"]
    assert monitor["scan_policy"]["max_files_per_scan"] == 37
    assert monitor["planning_profile"]["task_granularity"] == "detailed"
    exported_source = monitor["source_references"][0]
    assert exported_source["id"] == source_id
    assert exported_source["source_root_alias"] == shared_alias
    assert "source_root_id" not in exported_source
    assert monitor["task_source_references"] == [
        {
            "id": association_id,
            "task_id": task_id,
            "source_reference_id": source_id,
        }
    ]
    exported_journal = next(item for item in monitor["journals"] if item["id"] == journal_id)
    assert exported_journal["origin_key"] == "source:accepted-fingerprint"
    assert len(exported_journal["content_sha256"]) == 64

    with database.session() as session:
        project_root_id = session.scalar(
            select(ArtifactRoot.id).where(
                ArtifactRoot.project_id == project["id"],
                ArtifactRoot.is_project_root.is_(True),
            )
        )
        assert project_root_id is not None
