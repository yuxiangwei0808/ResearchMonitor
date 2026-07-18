from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from research_monitor.models import AuditEvent, OutboxEvent, Proposal, ProposalOperation, Task
from research_monitor.proposals import _is_high_risk_operation
from research_monitor.serializers import canonical_json

from .conftest import enroll, mutate
from .test_api import op


def _post_mutation(
    client: TestClient,
    project_id: str,
    revision: int,
    operations: list[dict],
    *,
    request_id: str | None = None,
):
    return client.post(
        f"/api/v1/projects/{project_id}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": request_id or str(uuid4()),
            "project_id": project_id,
            "base_semantic_revision": revision,
            "actor_type": "ui",
            "actor_label": "remediation-test",
            "operations": operations,
        },
    )


def _record_update_intent(
    client: TestClient,
    project_id: str,
    task_id: str,
    *,
    allow_completion: bool,
) -> dict:
    response = client.post(
        f"/api/v1/projects/{project_id}/agent-prompts",
        json={
            "api_version": "1",
            "schema_version": "1",
            "mode": "record_update",
            "scope_type": "task",
            "scope_id": task_id,
            "instructions": "Record my explicit confirmation that this task is complete.",
            "allow_completion": allow_completion,
            "artifact_locators": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _completion_proposal(
    project_id: str,
    revision: int,
    task_id: str,
    intent: dict,
) -> tuple[dict, list[str]]:
    task_operation_id = str(uuid4())
    journal_operation_id = str(uuid4())
    evidence = [
        {
            "kind": "user_instruction",
            "intent_id": intent["intent_id"],
            "summary": "The bound dashboard request explicitly confirms completion.",
        }
    ]
    payload = {
        "api_version": "1",
        "schema_version": "1",
        "proposal_contract_version": "2",
        "request_id": intent["proposal_request_id"],
        "project_id": project_id,
        "intent_id": intent["intent_id"],
        "base_semantic_revision": revision,
        "result_kind": "changes",
        "summary": "Record the confirmed completion",
        "rationale": "The user explicitly enabled completion for this request.",
        "scan_summary": {
            "files_considered": 0,
            "files_read": 0,
            "text_bytes_read": 0,
            "truncated": False,
            "limitations": "No repository scan was required.",
        },
        "operations": [
            {
                "id": task_operation_id,
                "type": "task.update",
                "entity_id": task_id,
                "expected_version": 1,
                "data": {
                    "status": "done",
                    "completion_summary": "The user confirmed the planned work is complete.",
                },
                "rationale": "Record the explicitly confirmed completion.",
                "confidence": 1,
                "basis": "user_instruction",
                "evidence": evidence,
            },
            {
                "id": journal_operation_id,
                "type": "journal.create",
                "entity_id": str(uuid4()),
                "data": {
                    "id": str(uuid4()),
                    "task_id": task_id,
                    "entry_type": "completion",
                    "content": "The user explicitly confirmed completion.",
                },
                "rationale": "Record the required task journal entry.",
                "confidence": 1,
                "basis": "user_instruction",
                "evidence": evidence,
            },
        ],
    }
    # A create operation's entity_id and data.id must identify the same row.
    payload["operations"][1]["data"]["id"] = payload["operations"][1]["entity_id"]
    return payload, [task_operation_id, journal_operation_id]


def test_bound_completion_survives_validate_create_revision_and_apply(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op(
                "task.create",
                {"id": task_id, "pipeline_id": pipeline_id, "title": "Finish analysis"},
            ),
        ],
    )
    intent = _record_update_intent(
        client, project["id"], task_id, allow_completion=True
    )
    payload, _operation_ids = _completion_proposal(
        project["id"], setup["semantic_revision"], task_id, intent
    )

    validated = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate", json=payload
    )
    assert validated.status_code == 200, validated.text
    created = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=payload
    )
    assert created.status_code == 201, created.text

    revision = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/revisions",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": setup["semantic_revision"],
            "actor_type": "ui",
            "actor_label": "Research Monitor UI",
            "summary": "Reviewed completion record",
            "rationale": "The human retained the bound completion confirmation.",
            "operations": payload["operations"],
        },
    )
    assert revision.status_code == 201, revision.text
    revised = revision.json()
    revised_operation_ids = [item["id"] for item in revised["operations"]]
    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{revised['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": revised_operation_ids,
        },
    )
    assert applied.status_code == 200, applied.text
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    task = next(item for item in snapshot["tasks"] if item["id"] == task_id)
    assert task["status"] == "done"
    assert task["completion_provenance"] == "agent"
    assert snapshot["journals"][0]["entry_type"] == "completion"


def test_bound_completion_requires_human_enabled_permission(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}),
        ],
    )
    intent = _record_update_intent(
        client, project["id"], task_id, allow_completion=False
    )
    payload, _operation_ids = _completion_proposal(
        project["id"], setup["semantic_revision"], task_id, intent
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate", json=payload
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "completion_not_authorized"


def test_bound_completion_rejects_forged_intent_and_mismatched_instruction(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}),
        ],
    )
    intent = _record_update_intent(
        client, project["id"], task_id, allow_completion=True
    )
    payload, _operation_ids = _completion_proposal(
        project["id"], setup["semantic_revision"], task_id, intent
    )

    forged = deepcopy(payload)
    forged["intent_id"] = str(uuid4())
    forged_response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate", json=forged
    )
    assert forged_response.status_code == 404
    assert forged_response.json()["detail"]["code"] == "intent_not_found"

    mismatched = deepcopy(payload)
    for operation in mismatched["operations"]:
        operation["evidence"][0]["intent_id"] = str(uuid4())
    for suffix in ("proposals/validate", "proposals"):
        response = client.post(
            f"/api/v1/projects/{project['id']}/{suffix}", json=mismatched
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "intent_evidence_mismatch"

    empty_completion_hash = deepcopy(payload)
    empty_completion_hash["operations"][0]["basis"] = "source_evidence"
    empty_completion_hash["operations"][0]["evidence"] = [
        {
            "kind": "completion_text",
            "summary": "A referenced completion must carry its exact content hash.",
            "source_reference_id": str(uuid4()),
            "content_hash": "",
        }
    ]
    completion_response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=empty_completion_hash,
    )
    assert completion_response.status_code == 422
    assert (
        completion_response.json()["detail"]["code"]
        == "invalid_completion_evidence"
    )

    empty_git_identity = deepcopy(payload)
    empty_git_identity["operations"][0]["data"] = {"status": "in_progress"}
    empty_git_identity["operations"][0]["basis"] = "source_evidence"
    empty_git_identity["operations"][0]["evidence"] = [
        {
            "kind": "git_metadata",
            "summary": "Git evidence with empty identity values is invalid.",
            "commit": "",
            "content_hash": "",
        }
    ]
    git_response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=empty_git_identity,
    )
    assert git_response.status_code == 422
    assert git_response.json()["detail"]["code"] == "invalid_git_evidence"


def test_bound_completion_staleness_is_rechecked_by_revision_and_apply(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}),
        ],
    )
    intent = _record_update_intent(
        client, project["id"], task_id, allow_completion=True
    )
    payload, operation_ids = _completion_proposal(
        project["id"], setup["semantic_revision"], task_id, intent
    )
    created = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=payload
    )
    assert created.status_code == 201, created.text
    current_project = client.get(
        f"/api/v1/projects/{project['id']}/snapshot",
        params={"sections": "project"},
    ).json()["project"]
    changed = mutate(
        client,
        project,
        setup["semantic_revision"],
        [
            op(
                "project.update",
                {"description": "A semantic edit after proposal creation."},
                project["id"],
                current_project["version"],
            )
        ],
    )

    revised = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/revisions",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": setup["semantic_revision"],
            "actor_type": "ui",
            "summary": "Stale completion revision",
            "operations": payload["operations"],
        },
    )
    assert revised.status_code == 409
    assert revised.json()["detail"]["code"] == "revision_conflict"

    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": operation_ids,
        },
    )
    assert applied.status_code == 409
    assert applied.json()["detail"]["code"] == "revision_conflict"
    assert changed["semantic_revision"] == setup["semantic_revision"] + 1


def test_intent_bound_stale_target_reports_entity_deleted_on_revise_and_apply(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op(
                "task.create",
                {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"},
            ),
        ],
    )
    intent = _record_update_intent(
        client, project["id"], task_id, allow_completion=True
    )
    payload, operation_ids = _completion_proposal(
        project["id"], setup["semantic_revision"], task_id, intent
    )
    created = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=payload
    )
    assert created.status_code == 201, created.text
    deleted = mutate(
        client,
        project,
        setup["semantic_revision"],
        [op("task.delete", {}, task_id, 1)],
    )

    revised = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/revisions",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": setup["semantic_revision"],
            "actor_type": "ui",
            "summary": "Stale guided task revision",
            "operations": payload["operations"],
        },
    )
    assert revised.status_code == 409, revised.text
    assert revised.json()["detail"]["code"] == "entity_deleted"

    apply_request = {
        "request_id": str(uuid4()),
        "selected_operation_ids": operation_ids,
    }
    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply",
        json=apply_request,
    )
    assert applied.status_code == 409, applied.text
    assert applied.json()["detail"]["code"] == "entity_deleted"
    retry = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply",
        json=apply_request,
    )
    assert retry.json() == applied.json()
    assert deleted["semantic_revision"] == setup["semantic_revision"] + 1


def test_apply_override_cannot_add_unauthorized_instruction_backed_completion(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}),
        ],
    )
    intent = _record_update_intent(
        client, project["id"], task_id, allow_completion=False
    )
    payload, operation_ids = _completion_proposal(
        project["id"], setup["semantic_revision"], task_id, intent
    )
    payload["operations"][0]["data"] = {"status": "in_progress"}
    created = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=payload
    )
    assert created.status_code == 201, created.text

    override = deepcopy(payload["operations"][0])
    override["data"] = {
        "status": "done",
        "completion_summary": "An apply-time edit attempted completion.",
    }
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{created.json()['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": operation_ids,
            "operation_overrides": [override],
        },
    )
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "completion_not_authorized"
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    task = next(item for item in snapshot["tasks"] if item["id"] == task_id)
    assert task["status"] == "planned"


def test_settings_noops_preserve_revision_intents_audit_and_outbox(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    intent_response = client.post(
        f"/api/v1/projects/{project['id']}/agent-prompts",
        json={
            "api_version": "1",
            "schema_version": "1",
            "mode": "initialize_structure",
            "scope_type": "project",
            "instructions": "Inspect only the empty synthetic monitor.",
            "allow_completion": False,
            "artifact_locators": [],
        },
    )
    assert intent_response.status_code == 201, intent_response.text
    intent = intent_response.json()

    proposal = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": 0,
            "summary": "Legacy draft for count coverage",
            "operations": [
                {
                    "id": str(uuid4()),
                    "type": "pipeline.create",
                    "entity_id": str(uuid4()),
                    "data": {"title": "Draft pipeline"},
                    "rationale": "The plan contains a pipeline heading.",
                    "confidence": 0.9,
                    "source_references": [
                        {"path": "PLAN.md", "anchor": "Test research plan"}
                    ],
                }
            ],
        },
    )
    assert proposal.status_code == 201, proposal.text

    with database.session() as session:
        audit_before = session.scalar(select(func.count()).select_from(AuditEvent))
        outbox_before = session.scalar(select(func.count()).select_from(OutboxEvent))

    initial_snapshot = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()
    sensitive_patterns = initial_snapshot["scan_policy"]["sensitive_patterns"]
    request_id = str(uuid4())
    operations = [
        op(
            "project.update",
            {
                "name": f"  {project['name']}  ",
                "description": "  ",
                "research_goal": "\n",
                "success_criteria": "\t",
                "color": "#4F46E5",
            },
            project["id"],
            project["version"],
        ),
        op(
            "planning_profile.update",
            {"preferred_pipeline_names": []},
            project["id"],
            1,
        ),
        op(
            "scan_policy.update",
            {
                "allow_git_metadata": True,
                "include_globs": [
                    " **/*.md ",
                    "**/*.md",
                    "**/*.txt",
                    "**/*.py",
                ],
                "sensitive_patterns": [
                    *sensitive_patterns,
                    sensitive_patterns[0].upper(),
                ],
            },
            project["id"],
            1,
        ),
    ]
    first = _post_mutation(
        client, project["id"], 0, operations, request_id=request_id
    )
    assert first.status_code == 200, first.text
    assert first.json()["semantic_changed"] is False
    assert first.json()["semantic_revision"] == 0
    assert [item["changed"] for item in first.json()["results"]] == [False] * 3
    retry = _post_mutation(
        client, project["id"], 0, operations, request_id=request_id
    )
    assert retry.status_code == 200
    assert retry.json() == first.json()

    snapshot = client.get(
        f"/api/v1/projects/{project['id']}/snapshot",
        params={"sections": "project,automation_state"},
    ).json()
    assert snapshot["project"]["version"] == 1
    assert snapshot["project"]["entity_version"] == 1
    assert snapshot["project"]["last_manual_update_at"] is None
    assert snapshot["automation_state"] == {
        "active_intent_count": 1,
        "open_draft_count": 1,
    }
    context = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": intent["intent_id"]},
    )
    assert context.status_code == 200, context.text
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == audit_before
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == outbox_before


def test_mixed_settings_envelope_advances_once_for_only_real_change(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    with database.session() as session:
        audit_before = session.scalar(select(func.count()).select_from(AuditEvent))
        outbox_before = session.scalar(select(func.count()).select_from(OutboxEvent))
    response = _post_mutation(
        client,
        project["id"],
        0,
        [
            op(
                "project.update",
                {"name": project["name"]},
                project["id"],
                project["version"],
            ),
            op(
                "planning_profile.update",
                {"preferred_pipeline_names": [" Data ", "data"]},
                project["id"],
                1,
            ),
        ],
    )
    assert response.status_code == 200, response.text
    assert response.json()["semantic_changed"] is True
    assert response.json()["semantic_revision"] == 1
    assert [item["changed"] for item in response.json()["results"]] == [False, True]
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    assert snapshot["project"]["version"] == 2
    assert snapshot["planning_profile"]["version"] == 2
    assert snapshot["planning_profile"]["preferred_pipeline_names"] == ["Data"]
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == audit_before + 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == outbox_before + 1


def test_automation_state_excludes_already_stale_intents_and_drafts(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    intent = client.post(
        f"/api/v1/projects/{project['id']}/agent-prompts",
        json={
            "api_version": "1",
            "schema_version": "1",
            "mode": "initialize_structure",
            "scope_type": "project",
            "instructions": "Create a bounded initial plan.",
            "allow_completion": False,
            "artifact_locators": [],
        },
    )
    assert intent.status_code == 201, intent.text
    draft = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": 0,
            "summary": "Draft that will become stale",
            "operations": [
                {
                    "id": str(uuid4()),
                    "type": "pipeline.create",
                    "entity_id": str(uuid4()),
                    "data": {"title": "Draft pipeline"},
                    "rationale": "The plan contains a pipeline heading.",
                    "confidence": 0.9,
                    "source_references": [
                        {"path": "PLAN.md", "anchor": "Test research plan"}
                    ],
                }
            ],
        },
    )
    assert draft.status_code == 201, draft.text
    before = client.get(
        f"/api/v1/projects/{project['id']}/snapshot",
        params={"sections": "project,automation_state"},
    ).json()
    assert before["automation_state"] == {
        "active_intent_count": 1,
        "open_draft_count": 1,
    }

    changed = mutate(
        client,
        project,
        0,
        [
            op(
                "project.update",
                {"description": "This makes both automation records stale."},
                project["id"],
                before["project"]["version"],
            )
        ],
    )
    assert changed["semantic_revision"] == 1
    after = client.get(
        f"/api/v1/projects/{project['id']}/snapshot",
        params={"sections": "automation_state"},
    ).json()
    assert after["automation_state"] == {
        "active_intent_count": 0,
        "open_draft_count": 0,
    }


def test_deleted_targets_and_archived_task_reject_immediate_and_legacy_updates(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id, journal_id, artifact_id = [str(uuid4()) for _ in range(4)]
    root_id = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()[
        "artifact_roots"
    ][0]["id"]
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "Task"}),
            op(
                "journal.create",
                {"id": journal_id, "task_id": task_id, "content": "Progress"},
            ),
            op(
                "artifact.create",
                {
                    "id": artifact_id,
                    "kind": "local",
                    "artifact_root_id": root_id,
                    "locator": "PLAN.md",
                },
            ),
        ],
    )
    revision = setup["semantic_revision"]
    for operation in (
        op("journal.delete", {}, journal_id, 1),
        op("artifact.delete", {}, artifact_id, 1),
        op("task.delete", {}, task_id, 1),
        op("pipeline.delete", {}, pipeline_id, 1),
    ):
        result = mutate(client, project, revision, [operation])
        revision = result["semantic_revision"]

    stale_updates = (
        op("journal.update", {"content": "stale"}, journal_id, 1),
        op("artifact.update", {"label": "stale"}, artifact_id, 1),
        op("task.update", {"priority": "required"}, task_id, 1),
        op("pipeline.update", {"title": "stale"}, pipeline_id, 1),
    )
    for operation in stale_updates:
        response = _post_mutation(client, project["id"], revision, [operation])
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "entity_deleted"

    legacy = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": revision,
            "summary": "Stale legacy update",
            "operations": [
                {
                    "id": str(uuid4()),
                    "type": "task.update",
                    "entity_id": task_id,
                    "expected_version": 1,
                    "data": {"priority": "required"},
                    "rationale": "A stale source requested this update.",
                    "confidence": 0.8,
                    "source_references": [{"path": "PLAN.md", "anchor": "Task"}],
                }
            ],
        },
    )
    assert legacy.status_code == 409, legacy.text
    assert legacy.json()["detail"]["code"] == "entity_deleted"

    active_pipeline, active_task = str(uuid4()), str(uuid4())
    active = mutate(
        client,
        project,
        revision,
        [
            op("pipeline.create", {"id": active_pipeline, "title": "Archived"}),
            op(
                "task.create",
                {"id": active_task, "pipeline_id": active_pipeline, "title": "Hidden"},
            ),
        ],
    )
    archived = mutate(
        client,
        project,
        active["semantic_revision"],
        [op("pipeline.archive", {}, active_pipeline, 1)],
    )
    inactive = _post_mutation(
        client,
        project["id"],
        archived["semantic_revision"],
        [op("task.update", {"priority": "required"}, active_task, 1)],
    )
    assert inactive.status_code == 409
    assert inactive.json()["detail"]["code"] == "entity_inactive"

    archived_delete = _post_mutation(
        client,
        project["id"],
        archived["semantic_revision"],
        [op("pipeline.delete", {"cascade": True}, active_pipeline, 2)],
    )
    assert archived_delete.status_code == 409
    assert archived_delete.json()["detail"]["code"] == "entity_inactive"


def test_deleted_artifact_locator_rejected_by_legacy_proposal_validation(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    root_id = snapshot["artifact_roots"][0]["id"]
    artifact_id = str(uuid4())
    created = mutate(
        client,
        project,
        0,
        [
            op(
                "artifact.create",
                {
                    "id": artifact_id,
                    "kind": "local",
                    "artifact_root_id": root_id,
                    "locator": "PLAN.md",
                },
            )
        ],
    )
    deleted = mutate(
        client,
        project,
        created["semantic_revision"],
        [op("artifact.delete", {}, artifact_id, 1)],
    )
    replacement_id = str(uuid4())

    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": deleted["semantic_revision"],
            "summary": "Recreate deleted artifact",
            "operations": [
                {
                    "id": str(uuid4()),
                    "type": "artifact.create",
                    "entity_id": replacement_id,
                    "data": {
                        "id": replacement_id,
                        "kind": "local",
                        "artifact_root_id": root_id,
                        "locator": "PLAN.md",
                    },
                    "rationale": "A source still names the deleted locator.",
                    "confidence": 0.8,
                    "source_references": [
                        {"path": "PLAN.md", "anchor": "Test research plan"}
                    ],
                }
            ],
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "entity_deleted"


def test_legacy_operations_are_never_selected_by_default(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    pipeline_id, first_task_id, second_task_id = [str(uuid4()) for _ in range(3)]
    pipeline_operation_id, first_operation_id, second_operation_id = [
        str(uuid4()) for _ in range(3)
    ]
    atomic_group_id = str(uuid4())
    source_references = [{"path": "PLAN.md", "anchor": "Test research plan"}]
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": 0,
            "summary": "Legacy custom plan",
            "operations": [
                {
                    "id": pipeline_operation_id,
                    "type": "pipeline.create",
                    "entity_id": pipeline_id,
                    "data": {"id": pipeline_id, "title": "Legacy pipeline"},
                    "rationale": "The source contains a planning heading.",
                    "confidence": 0.8,
                    "source_references": source_references,
                },
                {
                    "id": first_operation_id,
                    "type": "task.create",
                    "entity_id": first_task_id,
                    "atomic_group_id": atomic_group_id,
                    "prerequisite_operation_ids": [pipeline_operation_id],
                    "data": {
                        "id": first_task_id,
                        "pipeline_id": pipeline_id,
                        "title": "First task",
                    },
                    "rationale": "The source contains the first planned task.",
                    "confidence": 0.8,
                    "source_references": source_references,
                },
                {
                    "id": second_operation_id,
                    "type": "task.create",
                    "entity_id": second_task_id,
                    "atomic_group_id": atomic_group_id,
                    "prerequisite_operation_ids": [pipeline_operation_id],
                    "data": {
                        "id": second_task_id,
                        "pipeline_id": pipeline_id,
                        "title": "Second task",
                    },
                    "rationale": "The source contains the second planned task.",
                    "confidence": 0.8,
                    "source_references": source_references,
                },
            ],
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["proposal_contract_version"] == "1"
    assert len(response.json()["operations"]) == 3
    assert all(
        operation["default_selected"] is False
        for operation in response.json()["operations"]
    )


def test_stale_revision_and_apply_report_deleted_or_inactive_targets_durably(
    client: TestClient, project_root: Path, database
) -> None:
    project = enroll(client, project_root)
    pipeline_id, deleted_task_id, inactive_task_id = [str(uuid4()) for _ in range(3)]
    setup = mutate(
        client,
        project,
        0,
        [
            op("pipeline.create", {"id": pipeline_id, "title": "Work"}),
            op(
                "task.create",
                {
                    "id": deleted_task_id,
                    "pipeline_id": pipeline_id,
                    "title": "Delete me",
                },
            ),
            op(
                "task.create",
                {
                    "id": inactive_task_id,
                    "pipeline_id": pipeline_id,
                    "title": "Archive my pipeline",
                },
            ),
        ],
    )

    def legacy_operation(
        operation_type: str, task_id: str, data: dict
    ) -> dict:
        return {
            "id": str(uuid4()),
            "type": operation_type,
            "entity_id": task_id,
            "expected_version": 1,
            "data": data,
            "rationale": "The source supports this legacy task change.",
            "confidence": 0.8,
            "source_references": [
                {"path": "PLAN.md", "anchor": "Test research plan"}
            ],
        }

    delete_operation = legacy_operation(
        "task.update", deleted_task_id, {"priority": "required"}
    )
    inactive_operation = legacy_operation(
        "task.update", inactive_task_id, {"priority": "required"}
    )

    def create_draft(operation: dict) -> dict:
        response = client.post(
            f"/api/v1/projects/{project['id']}/proposals",
            json={
                "api_version": "1",
                "schema_version": "1",
                "request_id": str(uuid4()),
                "project_id": project["id"],
                "base_semantic_revision": setup["semantic_revision"],
                "summary": "Legacy stale-target draft",
                "operations": [operation],
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    deleted_draft = create_draft(delete_operation)
    inactive_draft = create_draft(inactive_operation)
    # Model a migrated pre-hardening custom draft. Current agent authority
    # correctly prevents creating fresh delete proposals, but stale historical
    # drafts still need precise target classification during application.
    with database.session() as session:
        stored_operation = session.get(ProposalOperation, delete_operation["id"])
        assert stored_operation is not None
        packed = json.loads(stored_operation.operation_json)
        packed["operation"]["type"] = "task.delete"
        packed["operation"]["data"] = {}
        stored_operation.operation_type = "task.delete"
        stored_operation.operation_json = canonical_json(packed)
    delete_operation["type"] = "task.delete"
    delete_operation["data"] = {}
    deleted = mutate(
        client,
        project,
        setup["semantic_revision"],
        [op("task.delete", {}, deleted_task_id, 1)],
    )
    archived = mutate(
        client,
        project,
        deleted["semantic_revision"],
        [op("pipeline.archive", {}, pipeline_id, 1)],
    )

    revised = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{inactive_draft['id']}/revisions",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": setup["semantic_revision"],
            "actor_type": "ui",
            "summary": "Stale graphical revision",
            "operations": [inactive_operation],
        },
    )
    assert revised.status_code == 409, revised.text
    assert revised.json()["detail"]["code"] == "entity_inactive"

    apply_request = {
        "request_id": str(uuid4()),
        "selected_operation_ids": [delete_operation["id"]],
    }
    applied = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{deleted_draft['id']}/apply",
        json=apply_request,
    )
    assert applied.status_code == 409, applied.text
    assert applied.json()["detail"]["code"] == "entity_deleted"
    retried = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{deleted_draft['id']}/apply",
        json=apply_request,
    )
    assert retried.status_code == 409, retried.text
    assert retried.json() == applied.json()

    with database.session() as session:
        stored_proposal = session.get(Proposal, deleted_draft["id"])
        stored_operation = session.get(ProposalOperation, delete_operation["id"])
        deleted_task = session.get(Task, deleted_task_id)
        inactive_task = session.get(Task, inactive_task_id)
        assert stored_proposal is not None and stored_proposal.status == "conflict"
        assert stored_proposal.closed_at is not None
        assert stored_operation is not None and stored_operation.disposition == "conflict"
        assert deleted_task is not None and deleted_task.deleted_at is not None
        assert deleted_task.entity_version == 2
        assert inactive_task is not None and inactive_task.priority == "recommended"
        assert inactive_task.entity_version == 1

    current = client.get(
        f"/api/v1/projects/{project['id']}/snapshot",
        params={"sections": "project"},
    ).json()["project"]
    assert current["semantic_revision"] == archived["semantic_revision"]


def test_source_text_evidence_rejects_empty_anchor_in_live_validation(
    client: TestClient, project_root: Path
) -> None:
    project = enroll(client, project_root)
    snapshot = client.get(f"/api/v1/projects/{project['id']}/snapshot").json()
    root_id = snapshot["artifact_roots"][0]["id"]
    intent_response = client.post(
        f"/api/v1/projects/{project['id']}/agent-prompts",
        json={
            "api_version": "1",
            "schema_version": "1",
            "mode": "initialize_structure",
            "scope_type": "project",
            "instructions": "Initialize only what the cited source supports.",
            "allow_completion": False,
            "artifact_locators": [],
        },
    )
    assert intent_response.status_code == 201, intent_response.text
    intent = intent_response.json()
    pipeline_id = str(uuid4())
    source_evidence = {
        "kind": "source_text",
        "source_root_id": root_id,
        "path": "PLAN.md",
        "anchor": "",
        "summary": "The plan names the initial research work.",
        "content_hash": hashlib.sha256(
            (project_root / "PLAN.md").read_bytes()
        ).hexdigest(),
    }
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json={
            "api_version": "1",
            "schema_version": "1",
            "proposal_contract_version": "2",
            "request_id": intent["proposal_request_id"],
            "project_id": project["id"],
            "intent_id": intent["intent_id"],
            "base_semantic_revision": 0,
            "result_kind": "changes",
            "summary": "Initialize from the plan",
            "rationale": "The cited source defines the first pipeline.",
            "scan_summary": {
                "files_considered": 1,
                "files_read": 1,
                "text_bytes_read": (project_root / "PLAN.md").stat().st_size,
                "truncated": False,
                "limitations": "",
            },
            "operations": [
                {
                    "id": str(uuid4()),
                    "type": "pipeline.create",
                    "entity_id": pipeline_id,
                    "data": {"id": pipeline_id, "title": "Research plan"},
                    "rationale": "Create the source-backed pipeline.",
                    "confidence": 0.9,
                    "basis": "source_evidence",
                    "evidence": [source_evidence],
                }
            ],
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_source_evidence"


@pytest.mark.parametrize(
    ("case", "operation"),
    [
        ("pipeline archive", {"type": "pipeline.archive", "data": {}}),
        ("pipeline update", {"type": "pipeline.update", "data": {"title": "x"}}),
        ("task move", {"type": "task.move", "data": {"position": 2}}),
        ("edge update", {"type": "edge.update", "data": {"enabled": False}}),
        ("journal update", {"type": "journal.update", "data": {"content": "x"}}),
        ("artifact update", {"type": "artifact.update", "data": {"label": "x"}}),
        ("task update pipeline", {"type": "task.update", "data": {"pipeline_id": "x"}}),
        ("task update parent", {"type": "task.update", "data": {"parent_id": "x"}}),
        ("task update position", {"type": "task.update", "data": {"position": 2}}),
        ("task update flow", {"type": "task.update", "data": {"child_flow_mode": "sequential"}}),
        ("task update outcome", {"type": "task.update", "data": {"outcome": "negative"}}),
        ("task update summary", {"type": "task.update", "data": {"completion_summary": "done"}}),
        ("task update source", {"type": "task.update", "data": {"completion_source": "user"}}),
        ("task update actor", {"type": "task.update", "data": {"completion_actor": "user"}}),
        ("task update provenance", {"type": "task.update", "data": {"completion_provenance": "manual"}}),
        ("task update override", {"type": "task.update", "data": {"completion_override_reason": "reason"}}),
        ("task update timestamp", {"type": "task.update", "data": {"completed_at": "2026-01-01T00:00:00Z"}}),
        ("task update done", {"type": "task.update", "data": {"status": "done"}}),
        ("task update dropped", {"type": "task.update", "data": {"status": "dropped"}}),
        ("task create done", {"type": "task.create", "data": {"status": "done"}}),
        ("task create dropped", {"type": "task.create", "data": {"status": "dropped"}}),
        ("task create outcome", {"type": "task.create", "data": {"outcome": "negative"}}),
        ("task create completion", {"type": "task.create", "data": {"completion_summary": "done"}}),
        ("task create provenance", {"type": "task.create", "data": {"completion_provenance": "agent"}}),
        ("edge create disabled", {"type": "edge.create", "data": {"disabled": True}}),
        ("edge create waiver", {"type": "edge.create", "data": {"waiver_reason": "approved"}}),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_server_risk_classifier_explicit_high_risk_matrix(
    case: str, operation: dict
) -> None:
    assert case
    assert _is_high_risk_operation(operation) is True


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "pipeline.create", "data": {"title": "Plan"}},
        {
            "type": "task.create",
            "data": {
                "status": "planned",
                "outcome": "not_applicable",
                "pipeline_id": "pipeline",
                "parent_id": "parent",
                "position": 2,
                "child_flow_mode": "sequential",
            },
        },
        {"type": "task.update", "data": {"status": "in_progress"}},
        {"type": "task.update", "data": {"priority": "required"}},
        {"type": "edge.create", "data": {"disabled": False}},
        {"type": "edge.create", "data": {"waiver_reason": ""}},
        {"type": "journal.create", "data": {"content": "Progress"}},
        {"type": "artifact.create", "data": {"locator": "result.txt"}},
    ],
)
def test_server_risk_classifier_keeps_representative_operations_normal(
    operation: dict,
) -> None:
    assert _is_high_risk_operation(operation) is False
