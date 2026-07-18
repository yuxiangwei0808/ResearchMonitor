from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from research_monitor.models import PlanningProfile
from research_monitor.serializers import canonical_json

from .conftest import enroll, mutate
from .test_api import op


def agent_op(
    operation_type: str,
    data: dict,
    *,
    entity_id: str | None = None,
    expected_version: int | None = None,
    source_references: list[dict] | None = None,
    evidence: list[dict | str] | None = None,
) -> dict:
    value = {
        "id": str(uuid4()),
        "type": operation_type,
        "data": data,
        "rationale": "The inspected source supports this proposed monitor change.",
        "confidence": 0.8,
        "evidence": evidence or [{"kind": "document", "locator": "PLAN.md"}],
        "source_references": source_references or [],
    }
    if entity_id:
        value["entity_id"] = entity_id
    if expected_version is not None:
        value["expected_version"] = expected_version
    return value


def proposal(project: dict, revision: int, operations: list[dict]) -> dict:
    return {
        "api_version": "1",
        "schema_version": "1",
        "request_id": str(uuid4()),
        "project_id": project["id"],
        "base_semantic_revision": revision,
        "summary": "Legacy custom proposal",
        "rationale": "Compatibility-path safety regression.",
        "actor_label": "Legacy Codex",
        "operations": operations,
    }


def setup_protected_tree(
    client: TestClient, project_root: Path,
) -> tuple[dict, int, dict[str, str]]:
    project = enroll(client, project_root)
    pipeline_id, parent_id, protected_id, other_id, artifact_id = [
        str(uuid4()) for _ in range(5)
    ]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "Research"}),
        op("task.create", {"id": parent_id, "pipeline_id": pipeline_id, "title": "Parent"}),
        op("task.create", {
            "id": protected_id,
            "pipeline_id": pipeline_id,
            "parent_id": parent_id,
            "title": "Protected child",
        }),
        op("task.create", {"id": other_id, "pipeline_id": pipeline_id, "title": "Other"}),
        op(
            "edge.create",
            {
                "id": str(uuid4()),
                "source_task_id": other_id,
                "target_task_id": protected_id,
            },
        ),
        op("artifact.create", {
            "id": artifact_id,
            "kind": "url",
            "locator": "https://example.test/run",
            "label": "Protected run",
        }),
        op("task_artifact.link", {
            "id": str(uuid4()),
            "task_id": protected_id,
            "artifact_id": artifact_id,
            "role": "evidence",
        }),
    ])
    profile = mutate(client, project, created["semantic_revision"], [
        op(
            "planning_profile.update",
            {"protected_task_ids": [protected_id]},
            project["id"],
            1,
        )
    ])
    return project, profile["semantic_revision"], {
        "pipeline": pipeline_id,
        "parent": parent_id,
        "protected": protected_id,
        "other": other_id,
        "artifact": artifact_id,
    }


def assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


def test_legacy_validate_rejects_current_and_indirect_protection_escapes(
    client: TestClient, project_root: Path,
) -> None:
    project, revision, ids = setup_protected_tree(client, project_root)
    cases = [
        agent_op(
            "task.update",
            {"description": "overwrite"},
            entity_id=ids["protected"],
            expected_version=1,
        ),
        agent_op(
            "task.move",
            {"position": 3},
            entity_id=ids["parent"],
            expected_version=1,
        ),
        agent_op(
            "task.move",
            {"position": 0},
            entity_id=ids["other"],
            expected_version=1,
        ),
        agent_op(
            "task.update",
            {"status": "dropped"},
            entity_id=ids["other"],
            expected_version=1,
        ),
        agent_op(
            "task.update",
            {
                "status": "done",
                "completion_summary": "The predecessor is complete.",
            },
            entity_id=ids["other"],
            expected_version=1,
            evidence=[{
                "kind": "completion_text",
                "summary": "The tracker explicitly marks this work complete.",
                "locator": "PLAN.md#complete",
            }],
        ),
        agent_op(
            "task.create",
            {
                "id": str(uuid4()),
                "pipeline_id": ids["pipeline"],
                "title": "Sequential sibling",
            },
        ),
        agent_op(
            "edge.create",
            {
                "id": str(uuid4()),
                "source_task_id": ids["other"],
                "target_task_id": ids["protected"],
            },
        ),
        agent_op(
            "journal.create",
            {"id": str(uuid4()), "task_id": ids["protected"], "content": "note"},
        ),
        agent_op(
            "task.create",
            {
                "id": str(uuid4()),
                "pipeline_id": ids["pipeline"],
                "parent_id": ids["protected"],
                "title": "Forbidden child",
            },
        ),
        agent_op(
            "pipeline.archive",
            {},
            entity_id=ids["pipeline"],
            expected_version=1,
        ),
        agent_op(
            "artifact.update",
            {"label": "overwrite protected evidence"},
            entity_id=ids["artifact"],
            expected_version=1,
        ),
    ]
    for operation in cases:
        response = client.post(
            f"/api/v1/projects/{project['id']}/proposals/validate",
            json=proposal(project, revision, [operation]),
        )
        assert_error(response, 403, "protected_entity")


def test_legacy_create_and_revision_recheck_protected_targets(
    client: TestClient, project_root: Path,
) -> None:
    project, revision, ids = setup_protected_tree(client, project_root)
    forbidden = agent_op(
        "task.update",
        {"priority": "required"},
        entity_id=ids["protected"],
        expected_version=1,
    )
    created_forbidden = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json=proposal(project, revision, [forbidden]),
    )
    assert_error(created_forbidden, 403, "protected_entity")

    safe_operation = agent_op(
        "task.update",
        {"description": "safe draft"},
        entity_id=ids["other"],
        expected_version=1,
    )
    safe_body = proposal(project, revision, [safe_operation])
    safe = client.post(f"/api/v1/projects/{project['id']}/proposals", json=safe_body)
    assert safe.status_code == 201, safe.text

    revision_body = {
        "api_version": "1",
        "schema_version": "1",
        "request_id": str(uuid4()),
        "project_id": project["id"],
        "base_semantic_revision": revision,
        "actor_type": "ui",
        "actor_label": "Human staging editor",
        "summary": "Unsafe graphical revision",
        "rationale": "Exercise canonical revalidation.",
        "operations": [forbidden],
    }
    revised = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{safe.json()['id']}/revisions",
        json=revision_body,
    )
    assert_error(revised, 403, "protected_entity")
    assert client.get(f"/api/v1/proposals/{safe.json()['id']}").json()["status"] == "draft"


def test_legacy_apply_rechecks_stored_draft_and_human_only_override(
    client: TestClient, project_root: Path, database,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    setup = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P"}),
        op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "T"}),
    ])
    operation = agent_op(
        "task.update",
        {"description": "safe"},
        entity_id=task_id,
        expected_version=1,
    )
    draft = client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json=proposal(project, setup["semantic_revision"], [operation]),
    )
    assert draft.status_code == 201, draft.text

    override = deepcopy(operation)
    override["data"] = {
        "description": "safe",
        "completion_override_reason": "skip unfinished descendants",
    }
    applied_override = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{draft.json()['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [operation["id"]],
            "operation_overrides": [override],
        },
    )
    assert_error(applied_override, 403, "human_only_completion_override")

    # Simulate a migrated pre-v0.2 open draft whose target became protected
    # without altering its recorded semantic base. Apply must not trust it.
    with database.session() as session:
        profile = session.get(PlanningProfile, project["id"])
        assert profile is not None
        profile.protected_task_ids_json = canonical_json([task_id])
    applied_stored = client.post(
        f"/api/v1/projects/{project['id']}/proposals/{draft.json()['id']}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": [operation["id"]],
        },
    )
    assert_error(applied_stored, 403, "protected_entity")


def test_legacy_limits_apply_only_to_new_or_restructured_tasks(
    client: TestClient, project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, first_id, second_id, third_id = [str(uuid4()) for _ in range(4)]
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "Deep"}),
        op("task.create", {"id": first_id, "pipeline_id": pipeline_id, "title": "L1"}),
        op("task.create", {
            "id": second_id, "pipeline_id": pipeline_id, "parent_id": first_id, "title": "L2",
        }),
        op("task.create", {
            "id": third_id, "pipeline_id": pipeline_id, "parent_id": second_id, "title": "L3",
        }),
    ])
    profile = mutate(client, project, created["semantic_revision"], [
        op(
            "planning_profile.update",
            {"max_nesting_depth": 2, "max_new_tasks_per_proposal": 1},
            project["id"],
            1,
        )
    ])
    revision = profile["semantic_revision"]

    untouched = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [agent_op(
            "pipeline.update", {"description": "metadata only"},
            entity_id=pipeline_id, expected_version=1,
        )]),
    )
    assert untouched.status_code == 200, untouched.text

    too_deep = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [agent_op("task.create", {
            "id": str(uuid4()), "pipeline_id": pipeline_id,
            "parent_id": third_id, "title": "L4",
        })]),
    )
    assert_error(too_deep, 422, "proposal_depth_limit")

    too_many = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [
            agent_op("task.create", {
                "id": str(uuid4()), "pipeline_id": pipeline_id, "title": "A",
            }),
            agent_op("task.create", {
                "id": str(uuid4()), "pipeline_id": pipeline_id, "title": "B",
            }),
        ]),
    )
    assert_error(too_many, 422, "proposal_task_limit")


def test_legacy_source_policy_and_artifact_url_rules_are_current(
    client: TestClient, project_root: Path,
) -> None:
    project = enroll(client, project_root)
    policy = mutate(client, project, 0, [
        op(
            "scan_policy.update",
            {"exclude_globs": ["private/**"]},
            project["id"],
            1,
        )
    ])
    revision = policy["semantic_revision"]

    excluded = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [agent_op(
            "pipeline.create", {"id": str(uuid4()), "title": "P"},
            source_references=[{"path": "private/secret.md"}],
        )]),
    )
    assert_error(excluded, 422, "source_excluded")

    unreadable = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [agent_op(
            "pipeline.create", {"id": str(uuid4()), "title": "P"},
            source_references=[{
                "source_root_id": str(uuid4()), "path": "PLAN.md",
            }],
        )]),
    )
    assert_error(unreadable, 422, "source_root_not_readable")

    missing = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [agent_op(
            "pipeline.create", {"id": str(uuid4()), "title": "P"},
            source_references=[{"path": "missing.md"}],
        )]),
    )
    assert_error(missing, 422, "source_unavailable")

    unsafe_evidence = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [agent_op(
            "pipeline.create",
            {"id": str(uuid4()), "title": "P"},
            evidence=[{
                "kind": "source_text",
                "summary": "A forged out-of-root citation.",
                "locator": "../../.env",
            }],
        )]),
    )
    assert_error(unsafe_evidence, 422, "unsafe_source_reference")

    outside = project_root.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (project_root / "linked.md").symlink_to(outside)
    symlinked = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, revision, [agent_op(
            "pipeline.create", {"id": str(uuid4()), "title": "P"},
            source_references=[{"path": "linked.md"}],
        )]),
    )
    assert_error(symlinked, 422, "source_symlink")

    for locator, code in (
        ("https://user:password@example.test/run", "artifact_url_credentials"),
        ("https://example.test/run?access_token=secret", "artifact_url_secret"),
    ):
        unsafe = client.post(
            f"/api/v1/projects/{project['id']}/proposals/validate",
            json=proposal(project, revision, [agent_op("artifact.create", {
                "id": str(uuid4()), "kind": "url", "locator": locator,
            })]),
        )
        assert_error(unsafe, 422, code)


def test_legacy_existing_completion_edit_rejects_unbound_instruction(
    client: TestClient, project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    created = mutate(client, project, 0, [
        op("pipeline.create", {"id": pipeline_id, "title": "P"}),
        op("task.create", {"id": task_id, "pipeline_id": pipeline_id, "title": "T"}),
    ])
    completed = mutate(client, project, created["semantic_revision"], [
        op(
            "task.update",
            {"status": "done", "completion_summary": "Manually confirmed"},
            task_id,
            1,
        )
    ])
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, completed["semantic_revision"], [agent_op(
            "task.update",
            {"completion_summary": "Agent rewrote the completion claim"},
            entity_id=task_id,
            expected_version=2,
            evidence=[{
                "kind": "user_instruction",
                "summary": "An unbound instruction says it is done.",
            }],
        )]),
    )
    assert_error(response, 422, "completion_evidence_required")


    forged = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(project, completed["semantic_revision"], [agent_op(
            "task.update",
            {"completion_summary": "A nonexistent artifact supposedly proves completion."},
            entity_id=task_id,
            expected_version=2,
            evidence=[{
                "kind": "result_evidence",
                "summary": "The final result supposedly exists.",
                "artifact_id": str(uuid4()),
            }],
        )]),
    )
    assert_error(forged, 422, "completion_evidence_required")
