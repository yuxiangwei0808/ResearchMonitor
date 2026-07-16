from __future__ import annotations

from uuid import uuid4

import pytest

from research_monitor.proposal_utils import proposal_fingerprint, topological_operations, validate_agent_operations
from research_monitor.schemas import Operation
from research_monitor.service import DomainError


def operation_graph() -> list[Operation]:
    pipeline = Operation(id=uuid4(), type="pipeline.create", entity_id=uuid4(), data={"title": "Pipeline"})
    task = Operation(id=uuid4(), type="task.create", entity_id=uuid4(), data={"pipeline_id": str(pipeline.entity_id), "title": "Task"}, prerequisite_operation_ids=[pipeline.id])
    return [task, pipeline]


def test_topological_order_and_uuid_independent_fingerprint() -> None:
    first = operation_graph(); second = operation_graph()
    # Entity IDs carry semantic identity, so make only transport IDs differ.
    second[0].entity_id = first[0].entity_id; second[1].entity_id = first[1].entity_id
    second[0].data["pipeline_id"] = first[0].data["pipeline_id"]
    assert [operation.type for operation in topological_operations(first)] == ["pipeline.create", "task.create"]
    assert proposal_fingerprint(first) == proposal_fingerprint(second)


def completion_operation(
    *,
    evidence: list[dict | str] | None = None,
    source_references: list[dict] | None = None,
) -> Operation:
    return Operation(
        type="task.update",
        entity_id=uuid4(),
        expected_version=1,
        data={"status": "done", "completion_summary": "The planned task completed."},
        rationale="The completion is explicitly documented.",
        confidence=0.9,
        evidence=evidence or [],
        source_references=source_references or [],
    )


def test_agent_authority_and_completion_evidence() -> None:
    with pytest.raises(DomainError):
        validate_agent_operations([Operation(type="project.trash", data={})])

    invalid_evidence = [
        ["train.py"],
        [{"path": "checkpoints/final.pt"}],
        [{"kind": "code", "summary": "The implementation exists.", "locator": "train.py"}],
        [{"kind": "smoke_test", "summary": "The command exited successfully."}],
        [{"kind": "external_url", "summary": "A run URL exists.", "locator": "https://wandb.ai/run"}],
    ]
    for evidence in invalid_evidence:
        with pytest.raises(DomainError) as error:
            validate_agent_operations([completion_operation(evidence=evidence)])
        assert error.value.code == "completion_evidence_required"

    with pytest.raises(DomainError) as source_only:
        validate_agent_operations([
            completion_operation(
                source_references=[{"path": "TRACKER.md", "anchor": "done"}]
            )
        ])
    assert source_only.value.code == "completion_evidence_required"


@pytest.mark.parametrize(
    "evidence",
    [
        {
            "kind": "completion_text",
            "summary": "The tracker explicitly marks this exact task complete.",
            "locator": "TRACKER.md#TASK-1",
        },
        {
            "kind": "user_instruction",
            "summary": "The user directly confirmed this exact task is complete.",
        },
        {
            "kind": "result_evidence",
            "summary": "The final metrics contain the planned comparison and outcome.",
            "locator": "results/final-metrics.json",
        },
    ],
)
def test_agent_completion_accepts_only_explicit_proof_categories(evidence: dict) -> None:
    validate_agent_operations([completion_operation(evidence=[evidence])])


def test_agent_completion_requires_summary_independently() -> None:
    operation = completion_operation(
        evidence=[{
            "kind": "user_instruction",
            "summary": "The user confirmed completion.",
        }]
    )
    operation.data["completion_summary"] = ""
    with pytest.raises(DomainError) as error:
        validate_agent_operations([operation])
    assert error.value.code == "completion_summary_required"


def test_task_artifact_link_fingerprint_ignores_fresh_link_uuid() -> None:
    task_id, artifact_id = uuid4(), uuid4()
    first = Operation(
        type="task_artifact.link", entity_id=uuid4(),
        data={"task_id": str(task_id), "artifact_id": str(artifact_id), "role": "evidence"},
        rationale="Attach the documented result", confidence=0.9, evidence=["results.json"],
    )
    second = first.model_copy(update={"id": uuid4(), "entity_id": uuid4()})

    assert proposal_fingerprint([first]) == proposal_fingerprint([second])
