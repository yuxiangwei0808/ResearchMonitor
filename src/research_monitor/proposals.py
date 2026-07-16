from __future__ import annotations

import json
import mimetypes
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import AGENT_OPERATION_SCHEMAS
from .models import (
    Artifact, ArtifactRoot, AuditEvent, IdempotencyRecord, OutboxEvent, Project, Proposal,
    ProposalOperation, ScanPolicy, SourceReference, Task, utcnow,
)
from .mutations import MutationService, SEMANTIC_OPERATION_TYPES
from .proposal_utils import (
    AGENT_OPERATION_TYPES,
    persist_source_references,
    proposal_fingerprint,
    topological_operations,
    validate_agent_operations,
)
from .schemas import MutationEnvelope, Operation, ProposalApply, ProposalEnvelope, ProposalRevision
from .preview import OpenedArtifact, SafeOpenError, open_regular_beneath
from .serializers import (
    canonical_json, jsonable, model_dict, pack_idempotent_response,
    request_fingerprint, unpack_idempotent_response,
)
from .service import (
    ARTIFACT_ROLES,
    EDGE_TYPES,
    FLOW_MODES,
    JOURNAL_TYPES,
    TASK_KINDS,
    TASK_OUTCOMES,
    TASK_PRIORITIES,
    TASK_STATUSES,
    DomainError,
    _public_artifact,
)


TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".json", ".csv", ".tsv", ".log", ".yaml", ".yml", ".toml", ".ini"}
MARKDOWN_EXTENSIONS = {".md", ".markdown"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
SECRET_PARTS = {
    ".env", ".ssh", ".aws", ".gnupg", "credential", "credentials", "secret", "token",
    "certificate", ".key", "keys", "private_key", "api_key", ".pem", ".p12", ".pfx", "id_rsa", "id_ed25519",
}


def _unpack_persisted_operation(value: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Read both legacy bare operations and records carrying immutable diffs."""
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("Persisted proposal operation must be a JSON object")
    operation = decoded.get("operation")
    if not isinstance(operation, dict):
        return decoded, None
    diff = decoded.get("diff")
    if not isinstance(diff, dict) or "before" not in diff or "after" not in diff:
        return operation, None
    return operation, {"before": diff["before"], "after": diff["after"]}


def _pack_persisted_operation(operation: Operation, diff: dict[str, Any]) -> str:
    """Keep storage backward-compatible without adding database columns."""
    return canonical_json(
        {
            "operation": operation.model_dump(mode="json"),
            "diff": {"before": diff.get("before"), "after": diff.get("after")},
        }
    )


class AppService(MutationService):
    def agent_context(self, session: Session, project_id: str) -> dict[str, Any]:
        context = super().agent_context(session, project_id)
        context["proposal_contract"] = {
            "api_version": context["proposal_contract"]["api_version"],
            "schema_version": context["proposal_contract"]["schema_version"],
            "actor": "agent",
            "operation_types": sorted(AGENT_OPERATION_TYPES),
            "operation_schemas": AGENT_OPERATION_SCHEMAS,
            "proposal_envelope_json_schema": ProposalEnvelope.model_json_schema(),
            "operation_json_schema": Operation.model_json_schema(),
            "enums": {
                "task_statuses": sorted(TASK_STATUSES),
                "task_priorities": sorted(TASK_PRIORITIES),
                "task_outcomes": sorted(TASK_OUTCOMES),
                "task_kinds": sorted(TASK_KINDS),
                "flow_modes": sorted(FLOW_MODES),
                "edge_types": sorted(EDGE_TYPES),
                "journal_types": sorted(JOURNAL_TYPES),
                "artifact_roles": sorted(ARTIFACT_ROLES),
            },
            "field_notes": {
                "ordering": "Use position for pipeline/task sibling order.",
                "journal.create": "Use content for the journal body.",
                "artifact.create.local": "Use artifact_root_id plus a root-relative locator.",
                "source_reference": "Use path, anchor, optional opaque_key, and optional fingerprint.",
            },
            "identity_rule": "Prefer monitor UUID/source reference; never merge by title alone.",
            "completion_rule": "Completion requires explicit text, user instruction, or unambiguous result evidence.",
        }
        context["open_proposal_drafts"] = self._open_proposal_draft_context(
            session, project_id
        )
        return context


    @staticmethod
    def _open_proposal_draft_context(
        session: Session, project_id: str
    ) -> list[dict[str, Any]]:
        """Expose reconciliation identities without leaking draft operation bodies."""
        drafts = session.scalars(
            select(Proposal)
            .where(Proposal.project_id == project_id, Proposal.status == "draft")
            .order_by(Proposal.created_at.desc())
        ).all()
        result: list[dict[str, Any]] = []
        for proposal in drafts:
            rows = session.scalars(
                select(ProposalOperation).where(
                    ProposalOperation.proposal_id == proposal.id
                )
            ).all()
            type_counts: dict[str, int] = {}
            identities: dict[str, dict[str, str]] = {}
            for row in rows:
                type_counts[row.operation_type] = type_counts.get(row.operation_type, 0) + 1
                try:
                    references = json.loads(row.source_references_json)
                except (TypeError, ValueError):
                    references = []
                if not isinstance(references, list):
                    continue
                for raw in references:
                    if not isinstance(raw, dict):
                        continue

                    def compact(*names: str, limit: int) -> str:
                        value = next(
                            (raw.get(name) for name in names if raw.get(name)), ""
                        )
                        return value[:limit] if isinstance(value, str) else ""

                    identity = {
                        key: value
                        for key, value in {
                            "monitor_reference_id": compact(
                                "monitor_reference_id", "id", limit=80
                            ),
                            "path": compact("path", "source_path", limit=1000),
                            "anchor": compact("anchor", limit=500),
                            "opaque_key": compact("opaque_key", limit=240),
                            "fingerprint": compact(
                                "fingerprint", "content_hash", limit=128
                            ),
                        }.items()
                        if value
                    }
                    if identity:
                        identities[canonical_json(identity)] = identity
            result.append(
                {
                    "id": proposal.id,
                    "base_semantic_revision": proposal.base_semantic_revision,
                    "summary": proposal.summary,
                    "operation_count": len(rows),
                    "operation_type_counts": dict(sorted(type_counts.items())),
                    "source_identity_count": len(identities),
                    "source_identities": [
                        identities[key] for key in sorted(identities)
                    ],
                    "created_at": jsonable(proposal.created_at),
                }
            )
        return result

    def validate_proposal(self, session: Session, project_id: str, envelope: ProposalEnvelope) -> dict[str, Any]:
        project = self._project(session, project_id)
        if envelope.project_id is not None and str(envelope.project_id) != project.id:
            raise DomainError(422, "project_mismatch", "Proposal project_id does not match the route")
        if project.semantic_revision != envelope.base_semantic_revision:
            raise DomainError(409, "revision_conflict", "Proposal was prepared from a stale project revision", {"expected": envelope.base_semantic_revision, "current": project.semantic_revision})
        operation_ids = {str(operation.id) for operation in envelope.operations}
        if len(operation_ids) != len(envelope.operations):
            raise DomainError(422, "duplicate_operation_id", "Proposal operation IDs must be unique")
        invalid_types = sorted({operation.type for operation in envelope.operations} - SEMANTIC_OPERATION_TYPES)
        if invalid_types:
            raise DomainError(422, "unknown_operation", "Unsupported proposal operation", invalid_types)
        validate_agent_operations(envelope.operations)
        ordered = topological_operations(envelope.operations)
        self._dry_run_operation_diffs(session, project, envelope, ordered)
        return {
            "valid": True,
            "project_id": project.id,
            "base_semantic_revision": envelope.base_semantic_revision,
            "operation_count": len(envelope.operations),
            "warnings": self._proposal_warnings(session, project, envelope.operations),
        }

    def _dry_run_operation_diffs(
        self,
        session: Session,
        project: Project,
        envelope: ProposalEnvelope,
        ordered: list[Operation],
    ) -> dict[str, dict[str, Any]]:
        """Validate and capture canonical operation state inside one savepoint."""
        request_id = uuid4()
        savepoint = session.begin_nested()
        try:
            result = self.mutate(
                session,
                MutationEnvelope(
                    request_id=request_id,
                    project_id=UUID(project.id),
                    base_semantic_revision=project.semantic_revision,
                    actor_type="agent",
                    actor_label=envelope.actor_label,
                    operations=ordered,
                ),
            )
            session.flush()
            return self._operation_diffs_from_mutation(
                session, str(request_id), ordered, result
            )
        finally:
            if savepoint.is_active:
                savepoint.rollback()
            session.expire_all()

    @staticmethod
    def _operation_diffs_from_mutation(
        session: Session,
        request_id: str,
        operations: list[Operation],
        result: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.request_id == request_id)
            .order_by(AuditEvent.sequence)
        ).all()
        results = result.get("results")
        if (
            not isinstance(results, list)
            or len(events) != len(operations)
            or len(results) != len(operations)
        ):
            raise DomainError(
                500,
                "proposal_diff_capture_failed",
                "Proposal validation did not produce one canonical diff per operation",
            )
        diffs: dict[str, dict[str, Any]] = {}
        for operation, event, operation_result in zip(
            operations, events, results, strict=True
        ):
            operation_id = str(operation.id)
            if (
                event.action != operation.type
                or event.entity_id != str(operation_result.get("entity_id"))
                or operation_result.get("operation_id") != operation_id
            ):
                raise DomainError(
                    500,
                    "proposal_diff_capture_failed",
                    "Proposal validation audit records did not align with operations",
                    {"operation_id": operation_id},
                )
            diffs[operation_id] = {
                "before": json.loads(event.before_json),
                "after": json.loads(event.after_json),
            }
        return diffs

    @staticmethod
    def _validate_operation_dependency_dag(operations: list[Operation]) -> None:
        adjacency = {str(operation.id): [str(value) for value in operation.prerequisite_operation_ids] for operation in operations}
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(node: str) -> None:
            if node in visited: return
            if node in visiting: raise DomainError(422, "proposal_dependency_cycle", "Proposal operation prerequisites contain a cycle")
            visiting.add(node)
            for neighbor in adjacency.get(node, []): visit(neighbor)
            visiting.remove(node); visited.add(node)
        for node in adjacency: visit(node)

    @staticmethod
    def _has_exact_source_identity(
        session: Session,
        project_id: str,
        references: list[dict[str, Any]],
    ) -> bool:
        for raw in references:
            reference_id = raw.get("id") or raw.get("monitor_reference_id")
            if reference_id:
                existing = session.get(SourceReference, str(reference_id))
                if existing is not None and existing.project_id == project_id:
                    return True
            source_path = str(raw.get("path") or raw.get("source_path") or "").replace("\\", "/")
            anchor = str(raw.get("anchor") or "")
            opaque_key = str(raw.get("opaque_key") or "")
            if source_path:
                existing = session.scalar(
                    select(SourceReference).where(
                        SourceReference.project_id == project_id,
                        SourceReference.source_path == source_path,
                        SourceReference.anchor == anchor,
                        SourceReference.opaque_key == opaque_key,
                    )
                )
                if existing is not None:
                    return True
            fingerprint = str(raw.get("fingerprint") or raw.get("content_hash") or "")
            if source_path and fingerprint:
                existing = session.scalar(
                    select(SourceReference).where(
                        SourceReference.project_id == project_id,
                        SourceReference.source_path == source_path,
                        SourceReference.anchor == anchor,
                        SourceReference.fingerprint == fingerprint,
                    )
                )
                if existing is not None:
                    return True
        return False

    @staticmethod
    def _proposal_warnings(session: Session, project: Project, operations: list[Operation]) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        titles = {
            str(operation.data.get("title", "")).strip().casefold(): str(operation.id)
            for operation in operations if operation.type == "task.create" and operation.data.get("title")
        }
        # Title equality is only a warning. It is never used as an identity match.
        existing_titles = {
            str(title).strip().casefold()
            for title in session.scalars(
                select(Task.title).where(Task.project_id == project.id, Task.deleted_at.is_(None))
            ).all()
        }
        if len(titles) < sum(operation.type == "task.create" and bool(operation.data.get("title")) for operation in operations):
            warnings.append({"code": "possible_duplicate_titles", "message": "Multiple proposed tasks share a title; stable IDs/source references are required for reconciliation."})
        for operation in operations:
            title = str(operation.data.get("title") or "").strip().casefold()
            if (
                operation.type == "task.create"
                and title in existing_titles
                and not AppService._has_exact_source_identity(session, project.id, operation.source_references)
            ):
                warnings.append({"code": "possible_duplicate_existing_task", "operation_id": str(operation.id), "message": "A proposed task shares a title with an active monitor task but has no exact source identity; review it as a possible duplicate."})

        for operation in operations:
            if operation.type == "task.update" and operation.data.get("status") == "done" and not operation.evidence:
                warnings.append({"code": "completion_without_evidence", "operation_id": str(operation.id), "message": "A proposed completion has no evidence reference."})
        return warnings

    @staticmethod
    def _proposal_request_identity(project_id: str, envelope: ProposalEnvelope) -> str:
        operations = sorted(
            (operation.model_dump(mode="json") for operation in envelope.operations),
            key=lambda operation: operation["id"],
        )
        return request_fingerprint({
            "action": "proposal.create",
            "project_id": project_id,
            "base_semantic_revision": envelope.base_semantic_revision,
            "summary": envelope.summary,
            "rationale": envelope.rationale,
            "actor_label": envelope.actor_label,
            "operations": operations,
        })

    def create_proposal(self, session: Session, project_id: str, envelope: ProposalEnvelope) -> dict[str, Any]:
        request_id = str(envelope.request_id)
        request_identity = self._proposal_request_identity(project_id, envelope)
        duplicate_request = session.get(IdempotencyRecord, request_id)
        if duplicate_request is not None:
            response, stored_fingerprint = unpack_idempotent_response(duplicate_request.response_json)
            if (
                duplicate_request.project_id != project_id
                or duplicate_request.action != "proposal.create"
                or (stored_fingerprint is not None and stored_fingerprint != request_identity)
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            existing_request = session.get(Proposal, str(response.get("proposal_id") or ""))
            if existing_request is None:
                raise DomainError(500, "idempotency_record_invalid", "Stored proposal request is unavailable")
            return self._public_proposal(session, existing_request)

        fingerprint = proposal_fingerprint(envelope.operations)
        existing_request = session.scalar(select(Proposal).where(Proposal.request_id == request_id))
        if existing_request is not None:
            if (
                existing_request.project_id != project_id
                or existing_request.fingerprint != fingerprint
                or existing_request.base_semantic_revision != envelope.base_semantic_revision
                or existing_request.summary != envelope.summary
                or existing_request.rationale != envelope.rationale
                or existing_request.actor_label != envelope.actor_label
            ):
                raise DomainError(409, "idempotency_collision", "Request ID is already used by another project")
            session.add(IdempotencyRecord(request_id=request_id, project_id=project_id, action="proposal.create", response_json=pack_idempotent_response({"proposal_id": existing_request.id}, request_identity)))
            return self._public_proposal(session, existing_request)
        project = self._project(session, project_id)
        if envelope.project_id is not None and str(envelope.project_id) != project.id:
            raise DomainError(422, "project_mismatch", "Proposal project_id does not match the route")
        if project.semantic_revision != envelope.base_semantic_revision:
            raise DomainError(409, "revision_conflict", "Proposal was prepared from a stale project revision", {"expected": envelope.base_semantic_revision, "current": project.semantic_revision})
        operation_ids = {str(operation.id) for operation in envelope.operations}
        if len(operation_ids) != len(envelope.operations):
            raise DomainError(422, "duplicate_operation_id", "Proposal operation IDs must be unique")
        validate_agent_operations(envelope.operations)
        ordered = topological_operations(envelope.operations)
        duplicates = session.scalars(
            select(Proposal).where(Proposal.project_id == project_id, Proposal.fingerprint == fingerprint).order_by(Proposal.created_at.desc())
        ).all()
        reusable = next(
            (
                proposal for proposal in duplicates
                if proposal.status != "draft" or proposal.base_semantic_revision == envelope.base_semantic_revision
            ),
            None,
        )
        if reusable is not None:
            session.add(IdempotencyRecord(request_id=request_id, project_id=project_id, action="proposal.create", response_json=pack_idempotent_response({"proposal_id": reusable.id}, request_identity)))
            return self._public_proposal(session, reusable)
        operation_diffs = self._dry_run_operation_diffs(
            session, project, envelope, ordered
        )
        for stale in duplicates:
            if stale.status == "draft":
                self._mark_proposal_conflict(
                    session, stale,
                    {"proposal_revision": stale.base_semantic_revision, "current": project.semantic_revision},
                )
        proposal = Proposal(
            id=str(UUID(request_id)), project_id=project_id, request_id=request_id,
            base_semantic_revision=envelope.base_semantic_revision, summary=envelope.summary,
            rationale=envelope.rationale, status="draft", fingerprint=fingerprint,
            actor_label=envelope.actor_label,
        )
        session.add(proposal)
        for operation in envelope.operations:
            session.add(
                ProposalOperation(
                    id=str(operation.id), proposal_id=proposal.id, operation_type=operation.type,
                    operation_json=_pack_persisted_operation(
                        operation, operation_diffs[str(operation.id)]
                    ),
                    atomic_group_id=str(operation.atomic_group_id) if operation.atomic_group_id else None,
                    prerequisites_json=canonical_json([str(value) for value in operation.prerequisite_operation_ids]),
                    rationale=operation.rationale, confidence=operation.confidence,
                    evidence_json=canonical_json(operation.evidence),
                    source_references_json=canonical_json(operation.source_references), disposition="pending",
                )
            )
        project.last_proposal_at = utcnow()
        session.add(IdempotencyRecord(request_id=request_id, project_id=project_id, action="proposal.create", response_json=pack_idempotent_response({"proposal_id": proposal.id}, request_identity)))
        session.add(OutboxEvent(project_id=project_id, event_type="proposal.created", payload_json=canonical_json({"proposal_id": proposal.id})))
        session.flush()
        return self._public_proposal(session, proposal)


    @staticmethod
    def _proposal_revision_request_identity(
        project_id: str, proposal_id: str, payload: ProposalRevision
    ) -> str:
        operations = sorted(
            (operation.model_dump(mode="json") for operation in payload.operations),
            key=lambda operation: operation["id"],
        )
        return request_fingerprint(
            {
                "action": "proposal.revise",
                "project_id": project_id,
                "proposal_id": proposal_id,
                "base_semantic_revision": payload.base_semantic_revision,
                "actor_type": "ui",
                "actor_label": payload.actor_label,
                "summary": payload.summary,
                "rationale": payload.rationale,
                "operations": operations,
            }
        )


    @staticmethod
    def _remap_revision_operations(operations: list[Operation]) -> list[Operation]:
        """Give replacement rows fresh proposal-local transport identities."""
        operation_ids = {operation.id: uuid4() for operation in operations}
        group_ids = {
            operation.atomic_group_id: uuid4()
            for operation in operations
            if operation.atomic_group_id is not None
        }
        return [
            operation.model_copy(
                update={
                    "id": operation_ids[operation.id],
                    "atomic_group_id": (
                        group_ids[operation.atomic_group_id]
                        if operation.atomic_group_id is not None
                        else None
                    ),
                    "prerequisite_operation_ids": [
                        operation_ids.get(value, value)
                        for value in operation.prerequisite_operation_ids
                    ],
                }
            )
            for operation in operations
        ]

    def revise_proposal(
        self,
        session: Session,
        project_id: str,
        proposal_id: str,
        payload: ProposalRevision,
    ) -> dict[str, Any]:
        """Atomically supersede an open draft with a validated human revision."""
        request_id = str(payload.request_id)
        request_identity = self._proposal_revision_request_identity(
            project_id, proposal_id, payload
        )
        duplicate = session.get(IdempotencyRecord, request_id)
        if duplicate is not None:
            response, stored_fingerprint = unpack_idempotent_response(
                duplicate.response_json
            )
            if (
                duplicate.project_id != project_id
                or duplicate.action != "proposal.revise"
                or stored_fingerprint != request_identity
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            replacement = session.get(
                Proposal, str(response.get("replacement_proposal_id") or "")
            )
            if replacement is None:
                raise DomainError(
                    500,
                    "idempotency_record_invalid",
                    "Stored proposal revision is unavailable",
                )
            return self._public_proposal(session, replacement)

        project = self._project(session, project_id)
        if str(payload.project_id) != project_id:
            raise DomainError(
                422, "project_mismatch", "Revision project_id does not match the route"
            )
        original = session.get(Proposal, proposal_id)
        if original is None or original.project_id != project_id:
            raise DomainError(404, "proposal_not_found", "Proposal not found")
        if original.status != "draft":
            raise DomainError(409, "proposal_closed", "Proposal is no longer open")
        if payload.base_semantic_revision != original.base_semantic_revision:
            raise DomainError(
                409,
                "proposal_revision_base_mismatch",
                "A replacement must retain the original proposal base revision",
                {
                    "proposal_revision": original.base_semantic_revision,
                    "requested": payload.base_semantic_revision,
                },
            )
        if project.semantic_revision != payload.base_semantic_revision:
            raise DomainError(
                409,
                "revision_conflict",
                "Proposal is stale and must be regenerated before editing",
                {
                    "proposal_revision": payload.base_semantic_revision,
                    "current": project.semantic_revision,
                },
            )

        operation_ids = {str(operation.id) for operation in payload.operations}
        if len(operation_ids) != len(payload.operations):
            raise DomainError(
                422, "duplicate_operation_id", "Proposal operation IDs must be unique"
            )
        invalid_types = sorted(
            {operation.type for operation in payload.operations}
            - SEMANTIC_OPERATION_TYPES
        )
        if invalid_types:
            raise DomainError(
                422, "unknown_operation", "Unsupported proposal operation", invalid_types
            )
        validate_agent_operations(payload.operations)
        topological_operations(payload.operations)
        replacement_operations = self._remap_revision_operations(payload.operations)
        ordered = topological_operations(replacement_operations)
        envelope = ProposalEnvelope(
            request_id=payload.request_id,
            project_id=payload.project_id,
            base_semantic_revision=payload.base_semantic_revision,
            summary=payload.summary,
            rationale=payload.rationale,
            actor_label=payload.actor_label,
            operations=replacement_operations,
        )
        operation_diffs = self._dry_run_operation_diffs(
            session, project, envelope, ordered
        )

        replacement = Proposal(
            id=request_id,
            project_id=project_id,
            request_id=request_id,
            base_semantic_revision=payload.base_semantic_revision,
            summary=payload.summary,
            rationale=payload.rationale,
            status="draft",
            fingerprint=proposal_fingerprint(replacement_operations),
            actor_label=payload.actor_label,
        )
        session.add(replacement)
        for operation in replacement_operations:
            session.add(
                ProposalOperation(
                    id=str(operation.id),
                    proposal_id=replacement.id,
                    operation_type=operation.type,
                    operation_json=_pack_persisted_operation(
                        operation, operation_diffs[str(operation.id)]
                    ),
                    atomic_group_id=(
                        str(operation.atomic_group_id)
                        if operation.atomic_group_id
                        else None
                    ),
                    prerequisites_json=canonical_json(
                        [str(value) for value in operation.prerequisite_operation_ids]
                    ),
                    rationale=operation.rationale,
                    confidence=operation.confidence,
                    evidence_json=canonical_json(operation.evidence),
                    source_references_json=canonical_json(
                        operation.source_references
                    ),
                    disposition="pending",
                )
            )

        original.status = "superseded"
        original.closed_at = utcnow()
        project.last_proposal_at = utcnow()
        response = {
            "superseded_proposal_id": original.id,
            "replacement_proposal_id": replacement.id,
        }
        session.add(
            IdempotencyRecord(
                request_id=request_id,
                project_id=project_id,
                action="proposal.revise",
                response_json=pack_idempotent_response(response, request_identity),
            )
        )
        session.add(
            OutboxEvent(
                project_id=project_id,
                event_type="proposal.superseded",
                payload_json=canonical_json(
                    {**response, "actor_type": "ui", "actor_label": payload.actor_label}
                ),
            )
        )
        session.flush()
        return self._public_proposal(session, replacement)

    def proposals(self, session: Session, project_id: str) -> list[dict[str, Any]]:
        self._project(session, project_id)
        proposals = session.scalars(select(Proposal).where(Proposal.project_id == project_id).order_by(Proposal.created_at.desc())).all()
        return [self._public_proposal(session, proposal) for proposal in proposals]

    def proposal(self, session: Session, proposal_id: str) -> dict[str, Any]:
        proposal = session.get(Proposal, proposal_id)
        if proposal is None:
            raise DomainError(404, "proposal_not_found", "Proposal not found")
        return self._public_proposal(session, proposal)

    @staticmethod
    def _proposal_supersession_links(
        session: Session, proposal: Proposal
    ) -> dict[str, str | None]:
        supersedes: str | None = None
        superseded_by: str | None = None
        events = session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.project_id == proposal.project_id,
                OutboxEvent.event_type == "proposal.superseded",
            )
            .order_by(OutboxEvent.id.desc())
        ).all()
        for event in events:
            try:
                payload = json.loads(event.payload_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("replacement_proposal_id") == proposal.id:
                supersedes = str(payload.get("superseded_proposal_id") or "") or None
            if payload.get("superseded_proposal_id") == proposal.id:
                superseded_by = (
                    str(payload.get("replacement_proposal_id") or "") or None
                )
            if supersedes is not None and superseded_by is not None:
                break
        return {
            "supersedes_proposal_id": supersedes,
            "superseded_by_proposal_id": superseded_by,
        }

    @staticmethod
    def _public_proposal(session: Session, proposal: Proposal) -> dict[str, Any]:
        rows = session.scalars(select(ProposalOperation).where(ProposalOperation.proposal_id == proposal.id)).all()
        operations = []
        for row in rows:
            operation, diff = _unpack_persisted_operation(row.operation_json)
            if diff is not None:
                operation["before"] = diff["before"]
                operation["after"] = diff["after"]
            operation["disposition"] = row.disposition
            operations.append(operation)
        links = AppService._proposal_supersession_links(session, proposal)
        return {
            "id": proposal.id, "project_id": proposal.project_id, "summary": proposal.summary,
            "rationale": proposal.rationale, "status": proposal.status,
            "base_semantic_revision": proposal.base_semantic_revision,
            "operations": operations, "created_at": jsonable(proposal.created_at),
            "closed_at": jsonable(proposal.closed_at), "actor_label": proposal.actor_label,
            "rejection_reason": proposal.rejection_reason,
            **links,
        }

    def apply_proposal(self, session: Session, project_id: str, proposal_id: str, payload: ProposalApply) -> dict[str, Any]:
        request_id = str(payload.request_id)
        apply_identity = request_fingerprint({
            "action": "proposal.apply",
            "project_id": project_id,
            "proposal_id": proposal_id,
            "selected_operation_ids": sorted(str(value) for value in payload.selected_operation_ids),
            "operation_overrides": sorted(
                (operation.model_dump(mode="json") for operation in payload.operation_overrides),
                key=lambda operation: operation["id"],
            ),
        })
        duplicate = session.get(IdempotencyRecord, request_id)
        if duplicate is not None:
            response, stored_fingerprint = unpack_idempotent_response(duplicate.response_json)
            if (
                duplicate.project_id != project_id
                or (stored_fingerprint is not None and stored_fingerprint != apply_identity)
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            if duplicate.action == "proposal.apply.conflict":
                raise DomainError(409, "revision_conflict", "Proposal is stale and must be regenerated", response)
            if duplicate.action != "mutation":
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            return response
        proposal = session.get(Proposal, proposal_id)
        if proposal is None or proposal.project_id != project_id:
            raise DomainError(404, "proposal_not_found", "Proposal not found")
        if proposal.status != "draft":
            raise DomainError(409, "proposal_closed", "Proposal is no longer open")
        project = self._project(session, project_id)
        if proposal.base_semantic_revision != project.semantic_revision:
            detail = {"proposal_revision": proposal.base_semantic_revision, "current": project.semantic_revision}
            self._mark_proposal_conflict(session, proposal, detail)
            session.add(IdempotencyRecord(request_id=request_id, project_id=project_id, action="proposal.apply.conflict", response_json=pack_idempotent_response(detail, apply_identity)))
            session.flush()
            raise DomainError(409, "revision_conflict", "Proposal is stale and must be regenerated", detail)
        rows = session.scalars(select(ProposalOperation).where(ProposalOperation.proposal_id == proposal.id)).all()
        by_id = {row.id: row for row in rows}; selected = {str(value) for value in payload.selected_operation_ids}
        unknown = selected - set(by_id)
        if unknown: raise DomainError(422, "unknown_operation_selection", "Selected operation is not in this proposal", sorted(unknown))
        for row_id in selected:
            missing = set(json.loads(by_id[row_id].prerequisites_json)) - selected
            if missing: raise DomainError(422, "selection_missing_prerequisite", "Selection omits required prerequisite operations", {"operation_id": row_id, "missing": sorted(missing)})
            group = by_id[row_id].atomic_group_id
            if group:
                group_ids = {row.id for row in rows if row.atomic_group_id == group}
                if not group_ids.issubset(selected): raise DomainError(422, "selection_splits_atomic_group", "All operations in an atomic group must be selected", {"group_id": group, "missing": sorted(group_ids - selected)})
        persisted_payloads = {
            row.id: _unpack_persisted_operation(row.operation_json) for row in rows
        }
        persisted = {
            row_id: Operation.model_validate(operation)
            for row_id, (operation, _diff) in persisted_payloads.items()
        }
        overrides = {str(operation.id): operation for operation in payload.operation_overrides}
        if len(overrides) != len(payload.operation_overrides):
            raise DomainError(422, "duplicate_operation_override", "Operation overrides must have unique IDs")
        unknown_overrides = set(overrides) - selected
        if unknown_overrides:
            raise DomainError(422, "invalid_operation_override", "Only selected proposal operations may be edited", sorted(unknown_overrides))
        for operation_id, override in overrides.items():
            original = persisted[operation_id]
            immutable_original = (
                original.type, original.resolved_entity_id(), original.atomic_group_id,
                tuple(original.prerequisite_operation_ids),
            )
            immutable_override = (
                override.type, override.resolved_entity_id(), override.atomic_group_id,
                tuple(override.prerequisite_operation_ids),
            )
            if immutable_override != immutable_original:
                raise DomainError(422, "immutable_operation_identity", "Editing cannot change operation type, target, atomic group, or prerequisites", {"operation_id": operation_id})
        effective = [overrides.get(row.id, persisted[row.id]) for row in rows if row.id in selected]
        validate_agent_operations(effective)
        operations = topological_operations(effective)
        envelope = MutationEnvelope(request_id=payload.request_id, project_id=UUID(project_id), base_semantic_revision=project.semantic_revision, actor_type="agent", actor_label=proposal.actor_label, operations=operations)
        result = self._mutate(session, envelope, idempotency_fingerprint=apply_identity)
        applied_diffs = self._operation_diffs_from_mutation(
            session, request_id, operations, result
        )
        for row in rows:
            override = overrides.get(row.id)
            if override is not None:
                _stored_operation, stored_diff = persisted_payloads[row.id]
                applied_diff = applied_diffs[row.id]
                row.operation_json = _pack_persisted_operation(
                    override,
                    {
                        "before": (
                            stored_diff["before"]
                            if stored_diff is not None
                            else applied_diff["before"]
                        ),
                        "after": applied_diff["after"],
                    },
                )
                row.rationale = override.rationale
                row.confidence = override.confidence
                row.evidence_json = canonical_json(override.evidence)
                row.source_references_json = canonical_json(override.source_references)
        persist_source_references(session, project_id, operations)
        proposal.status = "applied"; proposal.closed_at = utcnow(); project.last_agent_sync_at = utcnow()
        for row in rows: row.disposition = "applied" if row.id in selected else "rejected"
        session.add(OutboxEvent(project_id=project_id, event_type="proposal.applied", payload_json=canonical_json({"proposal_id": proposal.id, "selected_operation_ids": sorted(selected)})))
        session.flush()
        return result

    @staticmethod
    def _mark_proposal_conflict(session: Session, proposal: Proposal, detail: dict[str, Any]) -> None:
        proposal.status = "conflict"
        proposal.closed_at = utcnow()
        rows = session.scalars(select(ProposalOperation).where(ProposalOperation.proposal_id == proposal.id)).all()
        for row in rows:
            if row.disposition in {"pending", "selected"}:
                row.disposition = "conflict"
        session.add(
            OutboxEvent(
                project_id=proposal.project_id,
                event_type="proposal.conflict",
                payload_json=canonical_json({"proposal_id": proposal.id, **detail}),
            )
        )

    def reject_proposal(self, session: Session, project_id: str, proposal_id: str, request_id: str, reason: str) -> dict[str, Any]:
        reject_identity = request_fingerprint({
            "action": "proposal.reject",
            "project_id": project_id,
            "proposal_id": proposal_id,
            "reason": reason,
        })
        duplicate = session.get(IdempotencyRecord, request_id)
        if duplicate is not None:
            response, stored_fingerprint = unpack_idempotent_response(duplicate.response_json)
            if (
                duplicate.project_id != project_id
                or duplicate.action != "proposal.reject"
                or (stored_fingerprint is not None and stored_fingerprint != reject_identity)
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            return response
        proposal = session.get(Proposal, proposal_id)
        if proposal is None or proposal.project_id != project_id: raise DomainError(404, "proposal_not_found", "Proposal not found")
        if proposal.status != "draft": raise DomainError(409, "proposal_closed", "Proposal is no longer open")
        proposal.status = "rejected"; proposal.rejection_reason = reason; proposal.closed_at = utcnow()
        for row in session.scalars(select(ProposalOperation).where(ProposalOperation.proposal_id == proposal.id)): row.disposition = "rejected"
        response = {"proposal_id": proposal.id, "status": proposal.status}
        session.add(IdempotencyRecord(request_id=request_id, project_id=project_id, action="proposal.reject", response_json=pack_idempotent_response(response, reject_identity)))
        session.add(OutboxEvent(project_id=project_id, event_type="proposal.rejected", payload_json=canonical_json(response)))
        return response

    def history(self, session: Session, project_id: str, limit: int = 500) -> list[dict[str, Any]]:
        self._project(session, project_id)
        rows = session.scalars(select(AuditEvent).where(AuditEvent.project_id == project_id).order_by(AuditEvent.sequence.desc()).limit(min(limit, 2000))).all()
        result = []
        capabilities: dict[str, dict[str, Any]] = {}
        request_heads: set[str] = set()
        for row in rows:
            value = model_dict(row)
            value["event_type"] = value.pop("action")
            value["summary"] = value["event_type"].replace(".", " ").capitalize()
            request_id = value.get("request_id") or ""
            if request_id:
                if request_id not in capabilities:
                    capabilities[request_id] = self.undo_capability(session, project_id, request_id)
                value.update(capabilities[request_id])
                value["undo_request_head"] = request_id not in request_heads
                request_heads.add(request_id)
            else:
                value.update(undoable=False, undo_reason="This event is not part of a UI mutation request", undo_code="undo_not_available", undo_request_head=False)
            result.append(value)
        return result

    def events(self, session: Session, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        rows = session.scalars(select(OutboxEvent).where(OutboxEvent.id > after).order_by(OutboxEvent.id).limit(min(limit, 2000))).all()
        return [model_dict(row) for row in rows]

    @staticmethod
    def latest_event_id(session: Session) -> int:
        value = session.scalar(select(OutboxEvent.id).order_by(OutboxEvent.id.desc()).limit(1))
        return int(value or 0)

    def artifact_metadata(self, session: Session, artifact_id: str) -> dict[str, Any]:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None or artifact.deleted_at is not None: raise DomainError(404, "artifact_not_found", "Artifact not found")
        value = _public_artifact(session, artifact, refresh=True)
        opened: OpenedArtifact | None = None
        try:
            policy, opened = self._preview_policy(session, artifact)
            value.update(policy)
        except DomainError as exc:
            # Metadata remains useful when a root was moved, replaced, or is
            # temporarily unavailable. Preview access still fails closed.
            value.update({"previewable": False, "preview_reason": exc.message})
        finally:
            if opened is not None:
                opened.close()
        return value

    @staticmethod
    def _sensitive_preview_path(
        session: Session, artifact: Artifact, candidates: list[str],
    ) -> bool:
        policy = session.get(ScanPolicy, artifact.project_id)
        custom_patterns: list[str] = []
        if policy is not None:
            try:
                custom_patterns = [str(value).casefold() for value in json.loads(policy.sensitive_patterns_json or "[]")]
            except (TypeError, ValueError):
                custom_patterns = []
        for raw_pattern in [*SECRET_PARTS, *custom_patterns]:
            pattern = str(raw_pattern).replace("\\", "/").casefold().strip()
            if not pattern:
                continue
            has_glob = any(character in pattern for character in "*?[")
            for candidate in candidates:
                normalized = candidate.replace("\\", "/").casefold()
                components = [part.casefold() for part in Path(normalized).parts]
                matched = fnmatchcase(normalized, pattern) or any(
                    fnmatchcase(part, pattern) for part in components
                )
                if not has_glob:
                    matched = matched or any(pattern in part for part in components)
                if matched:
                    return True
        return False

    def _preview_policy(
        self, session: Session, artifact: Artifact,
    ) -> tuple[dict[str, Any], OpenedArtifact | None]:
        if artifact.locator_type != "local":
            return {"previewable": False, "preview_reason": "External URLs are never fetched"}, None
        if self._sensitive_preview_path(session, artifact, [artifact.locator]):
            return {"previewable": False, "preview_reason": "Sensitive paths are never previewed"}, None
        root = session.get(ArtifactRoot, artifact.root_id) if artifact.root_id else None
        if root is None or root.project_id != artifact.project_id:
            raise DomainError(404, "artifact_root_not_found", "Artifact root no longer exists")
        try:
            opened = open_regular_beneath(Path(root.root_path), artifact.locator)
        except SafeOpenError as exc:
            raise DomainError(exc.status_code, exc.code, exc.message) from exc
        if self._sensitive_preview_path(
            session, artifact, [artifact.locator, opened.resolved_relative],
        ):
            opened.close()
            return {"previewable": False, "preview_reason": "Sensitive paths are never previewed"}, None
        mime = mimetypes.guess_type(opened.name)[0] or "application/octet-stream"
        suffix = Path(opened.name).suffix.casefold()
        size = opened.size_bytes
        if suffix in MARKDOWN_EXTENSIONS and size <= 2 * 1024 * 1024:
            mode = "markdown"
        elif suffix in TEXT_EXTENSIONS and size <= 2 * 1024 * 1024:
            mode = "text"
        elif mime in IMAGE_MIMES and size <= 20 * 1024 * 1024:
            mode = "image"
        elif mime == "application/pdf" and size <= 50 * 1024 * 1024:
            mode = "pdf"
        else:
            opened.close()
            return {
                "previewable": False,
                "preview_reason": "File type or size is not safe for inline preview",
                "mime_type": mime,
                "size_bytes": size,
            }, None
        opened.media_type = mime
        opened.mode = mode
        return {
            "previewable": True,
            "preview_mode": mode,
            "mime_type": mime,
            "size_bytes": size,
        }, opened

    def artifact_preview(self, session: Session, artifact_id: str) -> OpenedArtifact:
        artifact = session.get(Artifact, artifact_id)
        if artifact is None or artifact.deleted_at is not None: raise DomainError(404, "artifact_not_found", "Artifact not found")
        policy, opened = self._preview_policy(session, artifact)
        if not policy.get("previewable"):
            raise DomainError(415, "preview_not_allowed", str(policy.get("preview_reason")))
        assert opened is not None
        return opened

    def export_project(self, session: Session, project_id: str) -> dict[str, Any]:
        snapshot = self.snapshot(session, project_id)
        project = dict(snapshot["project"]); project["root_path"] = None; project.pop("availability", None); project.pop("unavailable", None)
        snapshot["project"] = project
        aliases = {root["id"]: root["name"] for root in snapshot["artifact_roots"]}
        snapshot["artifact_roots"] = [{"alias": root["name"], "is_project_root": root["is_project_root"]} for root in snapshot["artifact_roots"]]
        for artifact in snapshot["artifacts"]:
            artifact["artifact_root_alias"] = aliases.get(artifact.pop("artifact_root_id", None)); artifact.pop("available", None); artifact.pop("mime_type", None); artifact.pop("size_bytes", None)
            artifact.pop("validation_warning", None)
        for collection in ("artifact_roots", "pipelines", "tasks", "edges", "journals", "artifacts", "task_artifacts", "layouts", "viewports"):
            values = snapshot.get(collection, [])
            snapshot[collection] = sorted(values, key=canonical_json)

        return {"schema_version": "1", "export_kind": "research-monitor-project", "project": snapshot}
