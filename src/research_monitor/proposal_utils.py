from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import AGENT_OPERATION_SCHEMAS, AGENT_OPERATION_TYPES
from .models import ArtifactRoot, SourceReference, Task, TaskSourceReference
from .schemas import Operation
from .serializers import canonical_json
from .service import DomainError


COMPLETION_EVIDENCE_KINDS = {
    "completion_text",
    "result_evidence",
}


def _completion_capable_evidence(
    operation: Operation,
    *,
    allow_guided_user_instruction: bool = False,
) -> bool:
    """Require an explicit proof category instead of treating any locator as proof.

    A bound ``user_instruction`` is only a potential proof here. Guided
    validation subsequently verifies the intent identity and its human-owned
    ``allow_completion`` permission. Unbound/legacy validation never enables
    this path.
    """
    for item in operation.evidence:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        if kind == "user_instruction":
            if allow_guided_user_instruction:
                return True
            continue
        if kind not in COMPLETION_EVIDENCE_KINDS:
            continue
        if kind == "completion_text":
            return True
        if any(
            str(item.get(field) or "").strip()
            for field in ("artifact_id", "source_reference_id", "content_hash")
        ):
            return True
    return False


def topological_operations(operations: Iterable[Operation]) -> list[Operation]:
    """Return prerequisites before consumers, preserving source order for ties."""
    values = list(operations)
    by_id = {str(operation.id): operation for operation in values}
    source_order = {str(operation.id): index for index, operation in enumerate(values)}
    outgoing: dict[str, list[str]] = defaultdict(list)
    degree = {operation_id: 0 for operation_id in by_id}
    for operation in values:
        operation_id = str(operation.id)
        for prerequisite in operation.prerequisite_operation_ids:
            prerequisite_id = str(prerequisite)
            if prerequisite_id not in by_id:
                raise DomainError(422, "missing_operation_prerequisite", "Operation prerequisite does not exist", {"operation_id": operation_id, "missing": prerequisite_id})
            outgoing[prerequisite_id].append(operation_id)
            degree[operation_id] += 1
    ready = sorted((value for value, count in degree.items() if count == 0), key=source_order.get)
    result: list[Operation] = []
    while ready:
        operation_id = ready.pop(0)
        result.append(by_id[operation_id])
        for consumer in sorted(outgoing.get(operation_id, []), key=source_order.get):
            degree[consumer] -= 1
            if degree[consumer] == 0:
                ready.append(consumer)
                ready.sort(key=source_order.get)
    if len(result) != len(values):
        raise DomainError(422, "proposal_dependency_cycle", "Proposal operation prerequisites contain a cycle")
    return result


def proposal_fingerprint(
    operations: Iterable[Operation], *, _contract_version: int = 1
) -> str:
    """Hash proposal semantics while ignoring transport-scoped operation/group UUIDs."""
    values = list(operations)

    def identity_list(items: list[Any]) -> list[Any]:
        if _contract_version == 1:
            return items
        return sorted(items, key=canonical_json)

    created_candidates: list[tuple[str, str]] = []
    for operation in values:
        contract = AGENT_OPERATION_SCHEMAS.get(operation.type)
        if contract is None or contract.get("mode") not in {"create", "link"}:
            continue
        raw_id = operation.resolved_entity_id()
        if not raw_id:
            continue
        identity = {
            "type": operation.type,
            "data": {
                key: value for key, value in operation.data.items() if key != "id"
            },
            "source_references": identity_list(operation.source_references),
            "rationale": operation.rationale,
        }
        if _contract_version == 2:
            identity.update({
                "basis": operation.basis,
                "confidence": operation.confidence,
                "evidence": identity_list(operation.evidence),
            })
        created_candidates.append((canonical_json(identity), str(raw_id)))
    created_ids = {
        raw_id: f"$created:{index}"
        for index, (_identity, raw_id) in enumerate(sorted(created_candidates))
    }

    def replace_created(value):
        if isinstance(value, str):
            return created_ids.get(value, value)
        if isinstance(value, dict):
            return {key: replace_created(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_created(item) for item in value]
        return value

    base: dict[str, dict] = {}
    signatures: dict[str, str] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    for operation in values:
        operation_id = str(operation.id)
        resolved_entity_id = operation.resolved_entity_id()
        value = {
            "type": operation.type,
            "entity_id": replace_created(str(resolved_entity_id)) if resolved_entity_id else None,
            "expected_version": operation.expected_version,
            "data": replace_created({key: item for key, item in operation.data.items() if key != "id"}),
            "rationale": operation.rationale,
            "confidence": operation.confidence,
            "evidence": identity_list(operation.evidence),
            "source_references": identity_list(operation.source_references),
        }
        if _contract_version == 2:
            value["basis"] = operation.basis
        base[operation_id] = value
        signatures[operation_id] = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
        if operation.atomic_group_id:
            groups[str(operation.atomic_group_id)].append(operation_id)

    canonical = []
    for operation in values:
        operation_id = str(operation.id)
        group_members = groups.get(str(operation.atomic_group_id), []) if operation.atomic_group_id else []
        canonical.append({
            **base[operation_id],
            "prerequisite_signatures": sorted(signatures[str(value)] for value in operation.prerequisite_operation_ids),
            "atomic_group_signatures": sorted(signatures[value] for value in group_members),
        })
    canonical.sort(key=canonical_json)
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def proposal_fingerprint_v2(
    *,
    intent_id: str,
    workflow_mode: str,
    scope_type: str,
    scope_id: str | None,
    result_kind: str,
    operations: Iterable[Operation],
    no_change_reason: str | None = None,
    evidence: list[dict] | None = None,
    source_references: list[dict] | None = None,
) -> str:
    """Hash typed proposal semantics independently from the frozen v1 hash."""

    return hashlib.sha256(
        canonical_json(
            {
                "fingerprint_version": 2,
                "intent_origin": intent_id,
                "workflow_mode": workflow_mode,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "result_kind": result_kind,
                "no_change_reason": no_change_reason,
                "operation_fingerprint": proposal_fingerprint(operations, _contract_version=2),
                "evidence": sorted(evidence or [], key=canonical_json),
                "source_references": sorted(source_references or [], key=canonical_json),
            }
        ).encode("utf-8")
    ).hexdigest()


def validate_agent_operations(
    operations: Iterable[Operation],
    *,
    allow_guided_user_instruction_completion: bool = False,
) -> None:
    for operation in operations:
        if operation.type not in AGENT_OPERATION_TYPES:
            raise DomainError(403, "agent_authority", f"Agents cannot propose {operation.type}")
        contract = AGENT_OPERATION_SCHEMAS[operation.type]
        data_contract = contract["data"]
        allowed = set(data_contract["required"]) | set(data_contract["optional"])
        unknown = sorted(set(operation.data) - allowed)
        if unknown:
            raise DomainError(
                422, "unknown_operation_field", "Operation data contains unsupported fields",
                {"operation_id": str(operation.id), "fields": unknown},
            )
        missing = [
            field for field in data_contract["required"]
            if field not in operation.data or operation.data[field] in {None, ""}
        ]
        if missing:
            raise DomainError(
                422, "missing_operation_field", "Operation data is missing required fields",
                {"operation_id": str(operation.id), "fields": missing},
            )
        if data_contract["at_least_one_field"] and not operation.data:
            raise DomainError(422, "empty_operation_update", "Update operation contains no changed fields")
        if contract["entity_id"] == "client_generated_required" and operation.resolved_entity_id() is None:
            raise DomainError(422, "missing_client_entity_id", "Create/link operation requires a client entity UUID")
        if contract["entity_id"] == "target_required" and operation.entity_id is None:
            raise DomainError(422, "missing_target_entity_id", "Update/archive operation requires the target entity UUID")
        if contract["expected_version"] == "required" and operation.expected_version is None:
            raise DomainError(422, "expected_version_required", "Existing entities require expected_version")
        if contract["expected_version"] == "forbidden" and operation.expected_version is not None:
            raise DomainError(422, "unexpected_entity_version", "Create operations cannot carry expected_version")
        if not operation.rationale.strip():
            raise DomainError(422, "operation_rationale_required", "Every agent operation requires rationale")
        if operation.confidence is None:
            raise DomainError(422, "operation_confidence_required", "Every agent operation requires confidence")
        if not operation.evidence and not operation.source_references:
            raise DomainError(422, "operation_evidence_required", "Every agent operation requires evidence or a source reference")
        if operation.type in {"task.create", "task.update"} and operation.data.get("status") == "done":
            if not str(operation.data.get("completion_summary") or "").strip():
                raise DomainError(
                    422,
                    "completion_summary_required",
                    "Agent-proposed completion requires a nonempty completion summary",
                    {"operation_id": str(operation.id)},
                )
            if not _completion_capable_evidence(
                operation,
                allow_guided_user_instruction=(
                    allow_guided_user_instruction_completion
                ),
            ):
                raise DomainError(
                    422,
                    "completion_evidence_required",
                    "Unbound agent completion requires structured completion_text or result_evidence proof",
                    {
                        "operation_id": str(operation.id),
                        "accepted_kinds": sorted(COMPLETION_EVIDENCE_KINDS),
                    },
                )


def persist_source_references(session: Session, project_id: str, operations: Iterable[Operation]) -> None:
    """Upsert accepted identities and attach each supported task use."""
    project_root = session.scalar(
        select(ArtifactRoot).where(
            ArtifactRoot.project_id == project_id,
            ArtifactRoot.is_project_root.is_(True),
        )
    )
    for operation in operations:
        if operation.type.startswith("task."):
            task_id = operation.data.get("id") or operation.entity_id
        elif operation.type in {"journal.create", "task_artifact.link"}:
            task_id = operation.data.get("task_id")
        else:
            task_id = None
        if not task_id:
            continue
        task = session.get(Task, str(task_id))
        if task is None or task.project_id != project_id:
            continue
        inline_sources = [
            item
            for item in operation.evidence
            if isinstance(item, dict)
            and item.get("source_root_id")
            and (item.get("path") or item.get("source_path"))
        ]
        for raw in [*operation.source_references, *inline_sources]:
            source_path = str(raw.get("path") or raw.get("source_path") or "").strip()
            if not source_path:
                continue
            normalized = PurePosixPath(source_path.replace("\\", "/"))
            if normalized.is_absolute() or ".." in normalized.parts:
                raise DomainError(422, "unsafe_source_reference", "Source references must be project-relative", {"path": source_path})
            source_path = normalized.as_posix()
            source_root_id = str(raw.get("source_root_id") or "") or (
                project_root.id if project_root is not None else None
            )
            anchor = str(raw.get("anchor") or "")
            fingerprint = str(raw.get("fingerprint") or raw.get("content_hash") or "")
            opaque_key = str(raw.get("opaque_key") or (task.user_key if task else "") or "")
            reference_id = raw.get("id") or raw.get("monitor_reference_id")
            existing = session.get(SourceReference, str(reference_id)) if reference_id else None
            if existing is not None and existing.project_id != project_id:
                raise DomainError(422, "source_reference_project_mismatch", "Source reference belongs to another project")
            if existing is None:
                existing = session.scalar(
                    select(SourceReference).where(
                        SourceReference.project_id == project_id,
                        SourceReference.source_root_id == source_root_id,
                        SourceReference.source_path == source_path,
                        SourceReference.anchor == anchor,
                        SourceReference.opaque_key == opaque_key,
                    )
                )
            if existing is None:
                existing = SourceReference(
                    id=str(uuid4()),
                    project_id=project_id,
                    task_id=str(task_id),
                    source_root_id=source_root_id,
                    source_path=source_path,
                    anchor=anchor,
                    opaque_key=opaque_key,
                    fingerprint=fingerprint,
                )
                session.add(existing)
                session.flush()
            else:
                if existing.source_root_id not in {None, source_root_id}:
                    raise DomainError(409, "source_identity_conflict", "Source identity root does not match")
                existing.source_root_id = source_root_id
                if existing.task_id is None:
                    existing.task_id = str(task_id)
                if fingerprint:
                    existing.fingerprint = fingerprint
            association = session.scalar(
                select(TaskSourceReference).where(
                    TaskSourceReference.task_id == str(task_id),
                    TaskSourceReference.source_reference_id == existing.id,
                )
            )
            if association is None:
                session.add(
                    TaskSourceReference(
                        id=str(uuid4()),
                        project_id=project_id,
                        task_id=str(task_id),
                        source_reference_id=existing.id,
                    )
                )
