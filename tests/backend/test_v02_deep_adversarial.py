from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import research_monitor.guided as guided
from research_monitor.models import Artifact

from .conftest import enroll, mutate
from .test_api import op
from .test_guided_skill_forward import (
    _instruction,
    _intent,
    _operation,
    _payload,
    _source,
)
from .test_legacy_proposal_safety import agent_op, assert_error, proposal


def _task_project(
    client: TestClient,
    project_root: Path,
    *,
    user_key: str | None = None,
) -> tuple[dict[str, Any], int, str, str, str]:
    project = enroll(client, project_root)
    pipeline_id, task_id = str(uuid4()), str(uuid4())
    task_data: dict[str, Any] = {
        "id": task_id,
        "pipeline_id": pipeline_id,
        "title": "Inspect the bounded evidence",
    }
    if user_key is not None:
        task_data["user_key"] = user_key
    changed = mutate(
        client,
        project,
        0,
        [
            op(
                "pipeline.create",
                {"id": pipeline_id, "title": "Analysis", "flow_mode": "freeform"},
            ),
            op("task.create", task_data),
        ],
    )
    root_id = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    ).json()["artifact_roots"][0]["id"]
    return project, changed["semantic_revision"], pipeline_id, task_id, root_id


def _reconcile_journal_payload(
    project: dict[str, Any],
    intent: dict[str, Any],
    revision: int,
    task_id: str,
    root_id: str,
    content: bytes,
    *,
    opaque_key: str | None = None,
) -> tuple[dict[str, Any], str]:
    evidence, reference = _source(root_id, "PLAN.md", content)
    if opaque_key is None:
        # Persistence may derive the task key, while a later scan may still
        # present the same source without an explicit opaque identity.
        reference.pop("opaque_key", None)
    else:
        reference["opaque_key"] = opaque_key
    journal_id = str(uuid4())
    operation = _operation(
        "journal.create",
        {
            "id": journal_id,
            "task_id": task_id,
            "entry_type": "progress",
            "content": "The bounded plan records concrete progress.",
        },
        entity_id=journal_id,
        basis="source_evidence",
        evidence=[evidence],
        source_references=[reference],
    )
    payload = _payload(
        project["id"],
        intent,
        revision,
        [operation],
        evidence=[evidence],
        source_references=[reference],
        scan_summary={
            "files_considered": 1,
            "files_read": 1,
            "text_bytes_read": len(content),
            "truncated": False,
            "limitations": [],
        },
    )
    return payload, str(operation["id"])


def _create_guided(
    client: TestClient,
    project_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(f"/api/v1/projects/{project_id}/proposals", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _apply(
    client: TestClient,
    project_id: str,
    proposal_id: str,
    selected: list[str],
    overrides: list[dict[str, Any]] | None = None,
):
    return client.post(
        f"/api/v1/projects/{project_id}/proposals/{proposal_id}/apply",
        json={
            "request_id": str(uuid4()),
            "selected_operation_ids": selected,
            "operation_overrides": overrides or [],
        },
    )


def _operation_override(public: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "id",
        "type",
        "entity_id",
        "expected_version",
        "data",
        "rationale",
        "confidence",
        "evidence",
        "source_references",
        "atomic_group_id",
        "prerequisite_operation_ids",
        "basis",
    }
    return {key: copy.deepcopy(value) for key, value in public.items() if key in fields}


def test_repeat_reconcile_deduplicates_open_draft_despite_private_origin_key(
    client: TestClient,
    project_root: Path,
) -> None:
    project, revision, _pipeline_id, task_id, root_id = _task_project(
        client, project_root
    )
    content = (project_root / "PLAN.md").read_bytes()
    first_intent = _intent(
        client,
        project["id"],
        mode="reconcile_progress",
        scope_type="task",
        scope_id=task_id,
        instructions="Reconcile the same bounded progress source.",
    )
    first_payload, _first_operation_id = _reconcile_journal_payload(
        project, first_intent, revision, task_id, root_id, content
    )
    first = _create_guided(client, project["id"], first_payload)
    assert "_origin_key" not in first["operations"][0]["data"]

    second_intent = _intent(
        client,
        project["id"],
        mode="reconcile_progress",
        scope_type="task",
        scope_id=task_id,
        instructions="Reconcile the same bounded progress source.",
    )
    assert second_intent["intent_id"] != first_intent["intent_id"]
    second_payload, _second_operation_id = _reconcile_journal_payload(
        project, second_intent, revision, task_id, root_id, content
    )
    second = _create_guided(client, project["id"], second_payload)

    assert second["id"] == first["id"]
    page = client.get(
        f"/api/v1/projects/{project['id']}/proposals",
        params={"summary": "true", "status": "open", "limit": 100},
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1


def test_accepted_reconcile_with_derived_opaque_key_rejects_same_journal_origin(
    client: TestClient,
    project_root: Path,
) -> None:
    project, revision, _pipeline_id, task_id, root_id = _task_project(
        client, project_root, user_key="TASK-OBSERVED-1"
    )
    content = (project_root / "PLAN.md").read_bytes()
    first_intent = _intent(
        client,
        project["id"],
        mode="reconcile_progress",
        scope_type="task",
        scope_id=task_id,
        instructions="Accept this source-grounded progress once.",
    )
    first_payload, first_operation_id = _reconcile_journal_payload(
        project, first_intent, revision, task_id, root_id, content
    )
    first = _create_guided(client, project["id"], first_payload)
    applied = _apply(
        client, project["id"], first["id"], [first_operation_id]
    )
    assert applied.status_code == 200, applied.text

    second_intent = _intent(
        client,
        project["id"],
        mode="reconcile_progress",
        scope_type="task",
        scope_id=task_id,
        instructions="Accept this source-grounded progress once.",
    )
    context = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": second_intent["intent_id"]},
    )
    assert context.status_code == 200, context.text
    assert any(
        item["opaque_key"] == "TASK-OBSERVED-1"
        for item in context.json()["source_identity_index"]["items"]
    )

    second_payload, _second_operation_id = _reconcile_journal_payload(
        project,
        second_intent,
        applied.json()["semantic_revision"],
        task_id,
        root_id,
        content,
    )
    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/proposals", json=second_payload
    )
    assert_error(duplicate, 409, "journal_origin_duplicate")


def test_distinct_opaque_source_identities_produce_distinct_journal_origins(
    client: TestClient,
    project_root: Path,
) -> None:
    project, revision, _pipeline_id, task_id, root_id = _task_project(
        client, project_root
    )
    content = (project_root / "PLAN.md").read_bytes()
    intent = _intent(
        client,
        project["id"],
        mode="reconcile_progress",
        scope_type="task",
        scope_id=task_id,
        instructions="Record both explicitly distinct source identities.",
    )
    first_payload, first_operation_id = _reconcile_journal_payload(
        project,
        intent,
        revision,
        task_id,
        root_id,
        content,
        opaque_key="SOURCE-A",
    )
    second_payload, second_operation_id = _reconcile_journal_payload(
        project,
        intent,
        revision,
        task_id,
        root_id,
        content,
        opaque_key="SOURCE-B",
    )
    first_payload["operations"].extend(second_payload["operations"])
    first_payload["source_references"].extend(
        second_payload["source_references"]
    )

    proposal = _create_guided(client, project["id"], first_payload)
    applied = _apply(
        client,
        project["id"],
        proposal["id"],
        [first_operation_id, second_operation_id],
    )
    assert applied.status_code == 200, applied.text
    snapshot = client.get(
        f"/api/v1/projects/{project['id']}/snapshot"
    )
    assert snapshot.status_code == 200, snapshot.text
    task_journals = [
        item
        for item in snapshot.json()["journals"]
        if item["task_id"] == task_id
    ]
    assert len(task_journals) == 2
    assert len({item["origin_key"] for item in task_journals}) == 2


def test_missing_position_projects_append_and_detects_protected_sequence_effect(
    client: TestClient,
    project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, protected_id = str(uuid4()), str(uuid4())
    created = mutate(
        client,
        project,
        0,
        [
            op(
                "pipeline.create",
                {"id": pipeline_id, "title": "Sequential", "flow_mode": "sequential"},
            ),
            op(
                "task.create",
                {
                    "id": protected_id,
                    "pipeline_id": pipeline_id,
                    "title": "Protected first task",
                    "position": 1,
                },
            ),
        ],
    )
    protected = mutate(
        client,
        project,
        created["semantic_revision"],
        [
            op(
                "planning_profile.update",
                {"protected_task_ids": [protected_id]},
                project["id"],
                1,
            )
        ],
    )
    intent = _intent(
        client,
        project["id"],
        mode="suggest_next_work",
        scope_type="pipeline",
        scope_id=pipeline_id,
        instructions="Suggest one following task without assigning an explicit position.",
    )
    evidence = [_instruction(intent["intent_id"])]
    new_task_id = str(uuid4())
    payload = _payload(
        project["id"],
        intent,
        protected["semantic_revision"],
        [
            _operation(
                "task.create",
                {
                    "id": new_task_id,
                    "pipeline_id": pipeline_id,
                    "title": "Appended candidate",
                },
                entity_id=new_task_id,
                basis="user_instruction",
                evidence=evidence,
            )
        ],
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate", json=payload
    )
    assert_error(response, 403, "protected_entity")
    assert response.json()["detail"]["details"]["sequence_edges"] == [
        [protected_id, new_task_id]
    ]


@pytest.mark.parametrize(
    "locator",
    ["https://example.test:not-a-port/run", "https://[::1/run"],
)
def test_guided_human_and_legacy_artifact_paths_reject_malformed_url_authorities(
    client: TestClient,
    project_root: Path,
    locator: str,
) -> None:
    project, revision, _pipeline_id, task_id, _root_id = _task_project(
        client, project_root
    )

    guided_response = client.post(
        f"/api/v1/projects/{project['id']}/agent-prompts",
        json={
            "api_version": "1",
            "schema_version": "1",
            "mode": "link_artifacts",
            "scope_type": "task",
            "scope_id": task_id,
            "instructions": "Link only this explicit locator.",
            "allow_completion": False,
            "artifact_locators": [{"kind": "url", "locator": locator}],
        },
    )
    assert_error(guided_response, 422, "unsafe_artifact_url")

    human_response = client.post(
        f"/api/v1/projects/{project['id']}/mutations",
        json={
            "api_version": "1",
            "schema_version": "1",
            "request_id": str(uuid4()),
            "project_id": project["id"],
            "base_semantic_revision": revision,
            "actor_type": "ui",
            "actor_label": "Malformed URL regression",
            "operations": [
                op(
                    "artifact.create",
                    {"id": str(uuid4()), "kind": "url", "locator": locator},
                )
            ],
        },
    )
    assert_error(human_response, 422, "unsafe_url")

    legacy_response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(
            project,
            revision,
            [
                agent_op(
                    "artifact.create",
                    {"id": str(uuid4()), "kind": "url", "locator": locator},
                )
            ],
        ),
    )
    assert_error(legacy_response, 422, "unsafe_artifact_url")


def test_scoped_context_redacts_preexisting_malformed_legacy_url(
    client: TestClient,
    project_root: Path,
    database,
) -> None:
    project, _revision, _pipeline_id, task_id, _root_id = _task_project(
        client, project_root
    )
    artifact_id = str(uuid4())
    malformed = "https://legacy.example:not-a-port/run?token=must-not-leak"
    with database.session() as session:
        session.add(
            Artifact(
                id=artifact_id,
                project_id=project["id"],
                root_id=None,
                locator_type="url",
                locator=malformed,
                provider="legacy",
                label="Malformed legacy URL",
            )
        )

    intent = _intent(
        client,
        project["id"],
        mode="reconcile_progress",
        scope_type="task",
        scope_id=task_id,
    )
    response = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": intent["intent_id"]},
    )
    assert response.status_code == 200, response.text
    item = next(
        value
        for value in response.json()["artifact_identity_index"]["items"]
        if value["id"] == artifact_id
    )
    assert item["redacted"] is True
    assert item["display_locator"] == "[redacted-invalid-url]"
    assert item["locator"].startswith("artifact-locator:")
    assert malformed not in json.dumps(response.json())


def test_artifact_apply_overrides_cannot_retarget_but_redacted_token_round_trips(
    client: TestClient,
    project_root: Path,
    database,
) -> None:
    project, revision, _pipeline_id, task_id, root_id = _task_project(
        client, project_root
    )
    locator = "https://wandb.ai/lab/project/runs/abc?view=summary#panel"
    intent = _intent(
        client,
        project["id"],
        mode="link_artifacts",
        scope_type="task",
        scope_id=task_id,
        artifact_locators=[{"kind": "url", "locator": locator, "provider": "W&B"}],
    )
    context = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": intent["intent_id"]},
    )
    assert context.status_code == 200, context.text
    explicit = context.json()["intent"]["explicit_artifact_locators"][0]
    token = explicit["locator"]
    assert token.startswith("intent-locator:")

    evidence = [_instruction(intent["intent_id"])]
    artifact_id, link_id, group_id = str(uuid4()), str(uuid4()), str(uuid4())
    artifact_operation = _operation(
        "artifact.create",
        {
            "id": artifact_id,
            "kind": "url",
            "locator": token,
            "provider": "W&B",
            "label": "Bound run",
        },
        entity_id=artifact_id,
        atomic_group_id=group_id,
        basis="user_instruction",
        evidence=evidence,
    )
    link_operation = _operation(
        "task_artifact.link",
        {
            "id": link_id,
            "task_id": task_id,
            "artifact_id": artifact_id,
            "role": "external_run",
        },
        entity_id=link_id,
        atomic_group_id=group_id,
        basis="user_instruction",
        evidence=evidence,
    )
    created = _create_guided(
        client,
        project["id"],
        _payload(
            project["id"], intent, revision, [artifact_operation, link_operation]
        ),
    )
    public_artifact = next(
        item for item in created["operations"] if item["type"] == "artifact.create"
    )
    assert public_artifact["data"]["locator"] == token
    assert locator not in json.dumps(created)
    selected = [str(artifact_operation["id"]), str(link_operation["id"])]

    invalid_data = [
        {**artifact_operation["data"], "kind": "local", "locator": locator},
        {
            **artifact_operation["data"],
            "locator": locator,
            "artifact_root_id": root_id,
        },
        {
            **artifact_operation["data"],
            "locator": "https://wandb.ai/lab/project/runs/different",
        },
    ]
    for data in invalid_data:
        override = copy.deepcopy(artifact_operation)
        override["data"] = data
        rejected = _apply(
            client, project["id"], created["id"], selected, [override]
        )
        assert_error(rejected, 422, "immutable_operation_identity")

    applied = _apply(
        client,
        project["id"],
        created["id"],
        selected,
        [copy.deepcopy(artifact_operation)],
    )
    assert applied.status_code == 200, applied.text
    with database.session() as session:
        stored = session.get(Artifact, artifact_id)
        assert stored is not None
        assert stored.locator_type == "url"
        assert stored.root_id is None
        assert stored.locator == locator


def test_edge_type_is_immutable_during_guided_apply_override(
    client: TestClient,
    project_root: Path,
) -> None:
    project = enroll(client, project_root)
    intent = _intent(
        client,
        project["id"],
        mode="initialize_structure",
        scope_type="project",
        instructions="Create two tasks with the explicit prerequisite between them.",
    )
    evidence = [_instruction(intent["intent_id"])]
    pipeline_id, first_id, second_id, edge_id = [str(uuid4()) for _ in range(4)]
    operations = [
        _operation(
            "pipeline.create",
            {"id": pipeline_id, "title": "Experiments", "flow_mode": "freeform"},
            entity_id=pipeline_id,
            basis="user_instruction",
            evidence=evidence,
        ),
        _operation(
            "task.create",
            {"id": first_id, "pipeline_id": pipeline_id, "title": "Prepare"},
            entity_id=first_id,
            basis="user_instruction",
            evidence=evidence,
        ),
        _operation(
            "task.create",
            {"id": second_id, "pipeline_id": pipeline_id, "title": "Run"},
            entity_id=second_id,
            basis="user_instruction",
            evidence=evidence,
        ),
        _operation(
            "edge.create",
            {
                "id": edge_id,
                "source_task_id": first_id,
                "target_task_id": second_id,
                "edge_type": "dependency",
            },
            entity_id=edge_id,
            basis="user_instruction",
            evidence=evidence,
        ),
    ]
    created = _create_guided(
        client,
        project["id"],
        _payload(project["id"], intent, 0, operations),
    )
    public_edge = next(
        item for item in created["operations"] if item["type"] == "edge.create"
    )
    override = _operation_override(public_edge)
    override["data"]["edge_type"] = "related"
    rejected = _apply(
        client,
        project["id"],
        created["id"],
        [item["id"] for item in created["operations"]],
        [override],
    )
    assert_error(rejected, 422, "immutable_operation_identity")


@pytest.mark.parametrize(
    ("cap_name", "cap_value", "scope_type", "detail_key", "limit_key"),
    [
        ("MAX_CONTEXT_SCOPE_TASKS", 1, "project", "task_count", "task_limit"),
        ("MAX_CONTEXT_INTERNAL_EDGES", 0, "project", "edge_count", "edge_limit"),
        (
            "MAX_CONTEXT_BOUNDARY_EDGES",
            0,
            "task",
            "boundary_edge_count",
            "boundary_edge_limit",
        ),
    ],
)
def test_scoped_context_caps_return_structured_413(
    client: TestClient,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap_name: str,
    cap_value: int,
    scope_type: str,
    detail_key: str,
    limit_key: str,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, first_id, second_id, edge_id = [str(uuid4()) for _ in range(4)]
    mutate(
        client,
        project,
        0,
        [
            op(
                "pipeline.create",
                {"id": pipeline_id, "title": "Graph", "flow_mode": "freeform"},
            ),
            op(
                "task.create",
                {"id": first_id, "pipeline_id": pipeline_id, "title": "First"},
            ),
            op(
                "task.create",
                {"id": second_id, "pipeline_id": pipeline_id, "title": "Second"},
            ),
            op(
                "edge.create",
                {
                    "id": edge_id,
                    "source_task_id": first_id,
                    "target_task_id": second_id,
                    "edge_type": "dependency",
                },
            ),
        ],
    )
    monkeypatch.setattr(guided, cap_name, cap_value)
    intent = _intent(
        client,
        project["id"],
        mode="reconcile_progress",
        scope_type=scope_type,
        scope_id=first_id if scope_type == "task" else None,
    )
    response = client.get(
        f"/api/v1/projects/{project['id']}/agent-context",
        params={"intent_id": intent["intent_id"]},
    )
    assert response.status_code == 413, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "context_scope_too_large"
    assert detail["details"][detail_key] > cap_value
    assert detail["details"][limit_key] == cap_value
    assert detail["details"]["recommended_action"] == "narrow_scope"


def test_deleted_protected_task_association_still_blocks_legacy_artifact_update(
    client: TestClient,
    project_root: Path,
) -> None:
    project, revision, pipeline_id, task_id, _root_id = _task_project(
        client, project_root
    )
    artifact_id, link_id = str(uuid4()), str(uuid4())
    linked = mutate(
        client,
        project,
        revision,
        [
            op(
                "artifact.create",
                {
                    "id": artifact_id,
                    "kind": "url",
                    "locator": "https://example.test/protected-run",
                    "label": "Protected result",
                },
            ),
            op(
                "task_artifact.link",
                {
                    "id": link_id,
                    "task_id": task_id,
                    "artifact_id": artifact_id,
                    "role": "evidence",
                },
            ),
        ],
    )
    protected = mutate(
        client,
        project,
        linked["semantic_revision"],
        [
            op(
                "planning_profile.update",
                {"protected_task_ids": [task_id]},
                project["id"],
                1,
            )
        ],
    )
    deleted = mutate(
        client,
        project,
        protected["semantic_revision"],
        [op("task.delete", {}, task_id, 1)],
    )
    assert pipeline_id
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(
            project,
            deleted["semantic_revision"],
            [
                agent_op(
                    "artifact.update",
                    {"label": "Agent must not rewrite this"},
                    entity_id=artifact_id,
                    expected_version=1,
                )
            ],
        ),
    )
    assert_error(response, 403, "protected_entity")


def test_tombstoned_incident_edge_still_blocks_legacy_edge_update(
    client: TestClient,
    project_root: Path,
) -> None:
    project = enroll(client, project_root)
    pipeline_id, protected_id, other_id, edge_id = [str(uuid4()) for _ in range(4)]
    created = mutate(
        client,
        project,
        0,
        [
            op(
                "pipeline.create",
                {"id": pipeline_id, "title": "Freeform", "flow_mode": "freeform"},
            ),
            op(
                "task.create",
                {"id": protected_id, "pipeline_id": pipeline_id, "title": "Protected"},
            ),
            op(
                "task.create",
                {"id": other_id, "pipeline_id": pipeline_id, "title": "Other"},
            ),
            op(
                "edge.create",
                {
                    "id": edge_id,
                    "source_task_id": protected_id,
                    "target_task_id": other_id,
                    "edge_type": "dependency",
                },
            ),
        ],
    )
    protected = mutate(
        client,
        project,
        created["semantic_revision"],
        [
            op(
                "planning_profile.update",
                {"protected_task_ids": [protected_id]},
                project["id"],
                1,
            )
        ],
    )
    deleted = mutate(
        client,
        project,
        protected["semantic_revision"],
        [op("edge.delete", {}, edge_id, 1)],
    )
    response = client.post(
        f"/api/v1/projects/{project['id']}/proposals/validate",
        json=proposal(
            project,
            deleted["semantic_revision"],
            [
                agent_op(
                    "edge.update",
                    {"edge_type": "related"},
                    entity_id=edge_id,
                    expected_version=2,
                )
            ],
        ),
    )
    assert_error(response, 403, "protected_entity")
