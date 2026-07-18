from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .contracts import AGENT_OPERATION_SCHEMAS
from .guided import (
    _redacted_explicit_locator,
    _resolve_intent_locator,
    issue_intent,
    prepare_guided_operations,
    public_intent,
    require_intent,
    scoped_agent_context,
    validate_top_level_v2_evidence,
)
from .models import (
    AgentIntent, Artifact, ArtifactRoot, AuditEvent, IdempotencyRecord,
    JournalEntry, OutboxEvent, Pipeline, Project, Proposal, ProposalOperation,
    ScanPolicy, SourceReference, Task, TaskEdge, TaskSourceReference, utcnow,
)
from .legacy_safety import validate_legacy_agent_constraints
from .mutations import MutationService, SEMANTIC_OPERATION_TYPES
from .proposal_utils import (
    AGENT_OPERATION_TYPES,
    persist_source_references,
    proposal_fingerprint,
    proposal_fingerprint_v2,
    topological_operations,
    validate_agent_operations,
)
from .schemas import AgentPromptCreate, MutationEnvelope, Operation, ProposalApply, ProposalEnvelope, ProposalRevision
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
    "cert", "certificate", ".key", "keys", "private_key", "api_key", ".pem", ".p12", ".pfx", ".crt", ".cer", ".der", "id_rsa", "id_ed25519",
}

PROPOSAL_STATUSES = frozenset(
    {"draft", "applied", "rejected", "conflict", "superseded", "no_changes"}
)
PROPOSAL_STATUS_FILTERS = PROPOSAL_STATUSES | {"open", "closed"}
PROPOSAL_SCOPE_TYPES = frozenset({"project", "pipeline", "task"})


HIGH_RISK_OPERATION_TYPES = frozenset({
    "pipeline.archive",
    "pipeline.update",
    "task.move",
    "edge.update",
    "journal.update",
    "artifact.update",
})
HIGH_RISK_TASK_FIELDS = frozenset({
    "pipeline_id",
    "parent_id",
    "position",
    "child_flow_mode",
    "outcome",
    "completion_summary",
    "completion_source",
    "completion_actor",
    "completion_provenance",
    "completion_override_reason",
    "completed_at",
})
HIGH_RISK_TASK_CREATE_FIELDS = frozenset({
    "completion_summary",
    "completion_source",
    "completion_actor",
    "completion_provenance",
    "completion_override_reason",
    "completed_at",
})


def _is_high_risk_operation(operation: dict[str, Any]) -> bool:
    operation_type = str(operation.get("type") or "")
    if operation_type in HIGH_RISK_OPERATION_TYPES:
        return True
    data = operation.get("data") if isinstance(operation.get("data"), dict) else {}
    terminal_status = str(data.get("status") or "") in {"done", "dropped"}
    if operation_type == "task.create":
        has_outcome = str(data.get("outcome") or "") not in {"", "not_applicable"}
        has_completion = any(
            data.get(field) is not None and data.get(field) != ""
            for field in HIGH_RISK_TASK_CREATE_FIELDS
        )
        return terminal_status or has_outcome or has_completion
    if operation_type == "task.update":
        return terminal_status or bool(set(data) & HIGH_RISK_TASK_FIELDS)
    if operation_type == "edge.create":
        return bool(data.get("disabled")) or bool(
            str(data.get("waiver_reason") or "").strip()
        )
    return False


def _stale_target_conflict(
    session: Session,
    project: Project,
    operations: list[Operation],
) -> DomainError | None:
    """Classify deleted/inactive targets before a generic stale-revision error.

    This is deliberately read-only. It does not attempt to validate the stale
    change set or its entity versions; it only gives a precise conflict when an
    operation still points at monitor state that was deleted or made inactive
    after the proposal was drafted.
    """

    created: dict[str, set[str]] = {
        "pipeline": set(),
        "task": set(),
        "artifact": set(),
    }
    for operation in operations:
        entity_id = operation.resolved_entity_id()
        if entity_id is None:
            continue
        if operation.type == "pipeline.create":
            created["pipeline"].add(str(entity_id))
        elif operation.type == "task.create":
            created["task"].add(str(entity_id))
        elif operation.type == "artifact.create":
            created["artifact"].add(str(entity_id))

    def conflict(
        code: str,
        message: str,
        operation: Operation,
        entity_id: str,
        *,
        field: str | None = None,
    ) -> DomainError:
        details = {
            "operation_id": str(operation.id),
            "operation_type": operation.type,
            "entity_id": entity_id,
        }
        if field is not None:
            details["field"] = field
        return DomainError(409, code, message, details)

    def pipeline_conflict(
        operation: Operation, pipeline_id: str, *, field: str | None = None
    ) -> DomainError | None:
        if not pipeline_id or pipeline_id in created["pipeline"]:
            return None
        pipeline = session.get(Pipeline, pipeline_id)
        if pipeline is None or pipeline.project_id != project.id:
            return None
        if pipeline.deleted_at is not None:
            return conflict(
                "entity_deleted",
                "Proposal targets a deleted pipeline",
                operation,
                pipeline.id,
                field=field,
            )
        if pipeline.archived_at is not None:
            return conflict(
                "entity_inactive",
                "Proposal targets an archived pipeline",
                operation,
                pipeline.id,
                field=field,
            )
        return None

    def task_conflict(
        operation: Operation, task_id: str, *, field: str | None = None
    ) -> DomainError | None:
        if not task_id or task_id in created["task"]:
            return None
        task = session.get(Task, task_id)
        if task is None or task.project_id != project.id:
            return None
        if task.deleted_at is not None:
            return conflict(
                "entity_deleted",
                "Proposal targets a deleted task",
                operation,
                task.id,
                field=field,
            )
        inactive = pipeline_conflict(operation, task.pipeline_id, field=field)
        if inactive is not None:
            return DomainError(
                409,
                "entity_inactive",
                "Proposal targets a task in an inactive pipeline",
                {
                    **(inactive.details or {}),
                    "entity_id": task.id,
                    "pipeline_id": task.pipeline_id,
                },
            )
        return None

    def artifact_conflict(
        operation: Operation, artifact_id: str, *, field: str | None = None
    ) -> DomainError | None:
        if not artifact_id or artifact_id in created["artifact"]:
            return None
        artifact = session.get(Artifact, artifact_id)
        if artifact is None or artifact.project_id != project.id:
            return None
        if artifact.deleted_at is not None:
            return conflict(
                "entity_deleted",
                "Proposal targets a deleted artifact",
                operation,
                artifact.id,
                field=field,
            )
        return None

    direct_models: dict[str, type[Any]] = {
        "pipeline.update": Pipeline,
        "pipeline.archive": Pipeline,
        "pipeline.delete": Pipeline,
        "task.update": Task,
        "task.move": Task,
        "task.delete": Task,
        "edge.update": TaskEdge,
        "edge.delete": TaskEdge,
        "journal.update": JournalEntry,
        "journal.delete": JournalEntry,
        "artifact.update": Artifact,
        "artifact.delete": Artifact,
    }
    for operation in operations:
        target_id = str(operation.entity_id or "")
        model = direct_models.get(operation.type)
        if model is Pipeline:
            value = pipeline_conflict(operation, target_id)
            if value is not None:
                return value
        elif model is Task:
            value = task_conflict(operation, target_id)
            if value is not None:
                return value
        elif model is Artifact:
            value = artifact_conflict(operation, target_id)
            if value is not None:
                return value
        elif model is JournalEntry:
            journal = session.get(JournalEntry, target_id)
            if journal is not None and journal.project_id == project.id:
                if journal.deleted_at is not None:
                    return conflict(
                        "entity_deleted",
                        "Proposal targets a deleted journal",
                        operation,
                        journal.id,
                    )
                value = task_conflict(operation, journal.task_id)
                if value is not None:
                    return DomainError(
                        409,
                        "entity_inactive",
                        "Proposal targets a journal on an inactive task",
                        {
                            **(value.details or {}),
                            "entity_id": journal.id,
                            "task_id": journal.task_id,
                        },
                    )
        elif model is TaskEdge:
            edge = session.get(TaskEdge, target_id)
            if edge is not None and edge.project_id == project.id:
                if edge.deleted_at is not None:
                    return conflict(
                        "entity_deleted",
                        "Proposal targets a deleted task edge",
                        operation,
                        edge.id,
                    )
                for field, task_id in (
                    ("source_task_id", edge.source_id),
                    ("target_task_id", edge.target_id),
                ):
                    value = task_conflict(operation, task_id, field=field)
                    if value is not None:
                        return DomainError(
                            409,
                            "entity_inactive",
                            "Proposal targets an edge with an inactive endpoint",
                            {
                                **(value.details or {}),
                                "entity_id": edge.id,
                                "task_id": task_id,
                            },
                        )

        references: list[tuple[str, str, str]] = []
        if operation.type in {"task.create", "task.update", "task.move"}:
            if operation.data.get("pipeline_id"):
                references.append(
                    ("pipeline", "pipeline_id", str(operation.data["pipeline_id"]))
                )
            if operation.data.get("parent_id"):
                references.append(
                    ("task", "parent_id", str(operation.data["parent_id"]))
                )
        if operation.type == "edge.create":
            references.extend(
                [
                    (
                        "task",
                        "source_task_id",
                        str(
                            operation.data.get("source_task_id")
                            or operation.data.get("source_id")
                            or ""
                        ),
                    ),
                    (
                        "task",
                        "target_task_id",
                        str(
                            operation.data.get("target_task_id")
                            or operation.data.get("target_id")
                            or ""
                        ),
                    ),
                ]
            )
        if operation.type == "journal.create":
            references.append(
                ("task", "task_id", str(operation.data.get("task_id") or ""))
            )
        if operation.type == "task_artifact.link":
            references.extend(
                [
                    ("task", "task_id", str(operation.data.get("task_id") or "")),
                    (
                        "artifact",
                        "artifact_id",
                        str(operation.data.get("artifact_id") or ""),
                    ),
                ]
            )
        for entity_type, field, entity_id in references:
            if entity_type == "pipeline":
                value = pipeline_conflict(operation, entity_id, field=field)
            elif entity_type == "task":
                value = task_conflict(operation, entity_id, field=field)
            else:
                value = artifact_conflict(operation, entity_id, field=field)
            if value is not None:
                return value

        if operation.type == "artifact.create":
            root_id = str(operation.data.get("artifact_root_id") or "") or None
            locator = str(operation.data.get("locator") or "")
            statement = select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.locator == locator,
                Artifact.deleted_at.is_not(None),
            )
            statement = (
                statement.where(Artifact.root_id == root_id)
                if root_id
                else statement.where(Artifact.root_id.is_(None))
            )
            tombstone = session.scalar(statement)
            if tombstone is not None:
                return conflict(
                    "entity_deleted",
                    "Artifact locator belongs to a deleted artifact",
                    operation,
                    tombstone.id,
                    field="locator",
                )
    return None


def _closure_safe_default_selection(operations: list[dict[str, Any]]) -> set[str]:
    selected = {
        str(operation.get("id"))
        for operation in operations
        if operation.get("basis") != "inference"
        and operation.get("risk") != "high"
    }
    atomic_groups: dict[str, set[str]] = {}
    for operation in operations:
        group_id = str(operation.get("atomic_group_id") or "")
        if group_id:
            atomic_groups.setdefault(group_id, set()).add(str(operation.get("id")))
    while True:
        rejected: set[str] = set()
        for operation in operations:
            operation_id = str(operation.get("id"))
            if operation_id not in selected:
                continue
            prerequisites = {str(value) for value in operation.get("prerequisite_operation_ids") or []}
            group_id = str(operation.get("atomic_group_id") or "")
            group_members = atomic_groups.get(group_id, {operation_id})
            if not prerequisites <= selected or not group_members <= selected:
                rejected.add(operation_id)
        if not rejected:
            return selected
        selected -= rejected


def _proposal_page_signature(
    *, status: str | None, workflow_mode: str | None, scope_type: str | None,
) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "status": status,
                "workflow_mode": workflow_mode,
                "scope_type": scope_type,
            }
        ).encode("utf-8")
    ).hexdigest()[:24]
def _encode_proposal_cursor(proposal: Proposal, signature: str) -> str:
    payload = canonical_json(
        {
            "v": 1,
            "created_at": jsonable(proposal.created_at),
            "id": proposal.id,
            "query": signature,
        }
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_proposal_cursor(cursor: str, signature: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(value, dict) or value.get("v") != 1:
            raise ValueError("unsupported cursor")
        proposal_id = str(UUID(str(value["id"])))
        if value.get("query") != signature:
            raise DomainError(
                422,
                "proposal_cursor_mismatch",
                "Proposal cursor was issued for different filters",
            )
        created_at = datetime.fromisoformat(
            str(value["created_at"]).replace("Z", "+00:00")
        )
        if created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
        return created_at, proposal_id
    except DomainError:
        raise
    except (binascii.Error, KeyError, TypeError, ValueError, UnicodeError) as exc:
        raise DomainError(
            422, "invalid_proposal_cursor", "Proposal cursor is invalid or malformed"
        ) from exc


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
    def create_agent_prompt(
        self, session: Session, project_id: str, payload: AgentPromptCreate
    ) -> dict[str, Any]:
        return issue_intent(self, session, project_id, payload)

    def agent_prompt(
        self, session: Session, project_id: str, intent_id: str
    ) -> dict[str, Any]:
        project = self._project(session, project_id)
        intent = session.get(AgentIntent, intent_id)
        if intent is None or intent.project_id != project.id:
            raise DomainError(404, "intent_not_found", "Guided request intent was not found")
        return public_intent(session, intent)

    def agent_context(
        self, session: Session, project_id: str, intent_id: str | None = None
    ) -> dict[str, Any]:
        if intent_id is not None:
            return scoped_agent_context(self, session, project_id, intent_id)
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
            "completion_rule": "Unbound completion requires explicit completion text or unambiguous result evidence; user instructions require a guided intent.",
        }
        context["open_proposal_drafts"] = self._open_proposal_draft_context(
            session, project_id
        )
        return context


    @staticmethod
    def _open_proposal_draft_context(
        session: Session, project_id: str, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Expose reconciliation identities without leaking draft operation bodies."""
        draft_query = (
            select(Proposal)
            .where(Proposal.project_id == project_id, Proposal.status == "draft")
            .order_by(Proposal.created_at.desc(), Proposal.id.desc())
        )
        if limit is not None:
            draft_query = draft_query.limit(limit)
        drafts = session.scalars(draft_query).all()
        draft_ids = [item.id for item in drafts]
        operation_rows = session.scalars(
            select(ProposalOperation)
            .where(ProposalOperation.proposal_id.in_(draft_ids))
            .order_by(ProposalOperation.proposal_id, ProposalOperation.id)
        ).all() if draft_ids else []
        rows_by_proposal: dict[str, list[ProposalOperation]] = {}
        for row in operation_rows:
            rows_by_proposal.setdefault(row.proposal_id, []).append(row)
        result: list[dict[str, Any]] = []
        for proposal in drafts:
            rows = rows_by_proposal.get(proposal.id, [])
            type_counts: dict[str, int] = {}
            identities: dict[str, dict[str, str]] = {}
            def record_identity(raw: dict[str, Any]) -> None:
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
                        "source_root_id": compact("source_root_id", limit=80),
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
            for row in rows:
                type_counts[row.operation_type] = type_counts.get(row.operation_type, 0) + 1
                for serialized in (
                    row.source_references_json,
                    row.evidence_json,
                ):
                    try:
                        decoded = json.loads(serialized)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(decoded, list):
                        continue
                    for raw in decoded:
                        if isinstance(raw, dict):
                            record_identity(raw)
            for serialized in (
                proposal.top_level_source_references_json,
                proposal.top_level_evidence_json,
            ):
                try:
                    decoded = json.loads(serialized)
                except (TypeError, ValueError):
                    continue
                if not isinstance(decoded, list):
                    continue
                for raw in decoded:
                    if isinstance(raw, dict):
                        record_identity(raw)
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
        if envelope.proposal_contract_version == "2":
            intent = require_intent(session, project, envelope.intent_id)
            if str(envelope.request_id) != intent.proposal_request_id:
                raise DomainError(
                    422,
                    "intent_request_mismatch",
                    "Proposal request_id does not match the request bound to this intent",
                )
            evidence, references = validate_top_level_v2_evidence(
                session, project, intent, envelope
            )
            if envelope.result_kind == "no_changes":
                return {
                    "valid": True,
                    "project_id": project.id,
                    "base_semantic_revision": envelope.base_semantic_revision,
                    "operation_count": 0,
                    "result_kind": "no_changes",
                    "workflow_mode": intent.workflow_mode,
                    "scope_type": intent.scope_type,
                    "scope_id": intent.scope_id,
                    "warnings": [],
                }
            _intent, effective, warnings = prepare_guided_operations(
                session, project, envelope
            )
            ordered = topological_operations(effective)
            self._dry_run_operation_diffs(session, project, envelope, ordered)
            return {
                "valid": True,
                "project_id": project.id,
                "base_semantic_revision": envelope.base_semantic_revision,
                "operation_count": len(effective),
                "result_kind": "changes",
                "workflow_mode": intent.workflow_mode,
                "scope_type": intent.scope_type,
                "scope_id": intent.scope_id,
                "warnings": warnings,
                "derived_operations": [item.model_dump(mode="json") for item in effective],
            }
        operation_ids = {str(operation.id) for operation in envelope.operations}
        if len(operation_ids) != len(envelope.operations):
            raise DomainError(422, "duplicate_operation_id", "Proposal operation IDs must be unique")
        invalid_types = sorted({operation.type for operation in envelope.operations} - SEMANTIC_OPERATION_TYPES)
        if invalid_types:
            raise DomainError(422, "unknown_operation", "Unsupported proposal operation", invalid_types)
        validate_agent_operations(envelope.operations)
        validate_legacy_agent_constraints(session, project, envelope.operations)
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

    @staticmethod
    def _guided_proposal_request_identity(
        project_id: str, envelope: ProposalEnvelope
    ) -> str:
        return request_fingerprint(
            {
                "action": "proposal.create",
                "project_id": project_id,
                "payload": envelope.model_dump(mode="json"),
            }
        )

    @staticmethod
    def _guided_semantic_fingerprint(
        *,
        intent_id: str,
        workflow_mode: str,
        scope_type: str,
        scope_id: str | None,
        operations: list[Operation],
        evidence: list[dict[str, Any]],
        source_references: list[dict[str, Any]],
    ) -> str:
        def normalize(items: list[Any]) -> list[Any]:
            normalized: list[Any] = []
            for item in items:
                if (
                    isinstance(item, dict)
                    and item.get("kind") == "user_instruction"
                    and str(item.get("intent_id") or "") == intent_id
                ):
                    normalized.append({**item, "intent_id": "$bound-intent"})
                else:
                    normalized.append(item)
            return normalized

        normalized_operations = [
            operation.model_copy(
                update={
                    "data": {
                        key: value for key, value in operation.data.items()
                        if not key.startswith("_")
                    },
                    "evidence": normalize(operation.evidence),
                }
            )
            for operation in operations
        ]
        return proposal_fingerprint_v2(
            intent_id="$semantic-open-draft",
            workflow_mode=workflow_mode,
            scope_type=scope_type,
            scope_id=scope_id,
            result_kind="changes",
            operations=normalized_operations,
            evidence=normalize(evidence),
            source_references=source_references,
        )

    def _create_guided_proposal(
        self,
        session: Session,
        project_id: str,
        envelope: ProposalEnvelope,
    ) -> dict[str, Any]:
        request_id = str(envelope.request_id)
        request_identity = self._guided_proposal_request_identity(project_id, envelope)
        duplicate = session.get(IdempotencyRecord, request_id)
        if duplicate is not None:
            response, stored_fingerprint = unpack_idempotent_response(
                duplicate.response_json
            )
            if (
                duplicate.project_id != project_id
                or duplicate.action != "proposal.create"
                or stored_fingerprint != request_identity
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            proposal = session.get(Proposal, str(response.get("proposal_id") or ""))
            if proposal is None:
                raise DomainError(500, "idempotency_record_invalid", "Stored proposal request is unavailable")
            return self._public_proposal(session, proposal)

        project = self._project(session, project_id)
        if envelope.project_id is not None and str(envelope.project_id) != project.id:
            raise DomainError(422, "project_mismatch", "Proposal project_id does not match the route")
        if project.semantic_revision != envelope.base_semantic_revision:
            raise DomainError(
                409,
                "revision_conflict",
                "Proposal was prepared from a stale project revision",
                {
                    "expected": envelope.base_semantic_revision,
                    "current": project.semantic_revision,
                },
            )
        intent = require_intent(session, project, envelope.intent_id)
        if request_id != intent.proposal_request_id:
            raise DomainError(
                422, "intent_request_mismatch",
                "Proposal request UUID is not bound to this intent",
            )
        top_evidence, top_references = validate_top_level_v2_evidence(
            session, project, intent, envelope
        )
        if envelope.result_kind == "changes":
            _intent, effective, warnings = prepare_guided_operations(
                session, project, envelope
            )
            ordered = topological_operations(effective)
            operation_diffs = self._dry_run_operation_diffs(
                session, project, envelope, ordered
            )
        else:
            effective = []
            warnings = []
            operation_diffs = {}
        fingerprint = proposal_fingerprint_v2(
            intent_id=intent.id,
            workflow_mode=intent.workflow_mode,
            scope_type=intent.scope_type,
            scope_id=intent.scope_id,
            result_kind=envelope.result_kind,
            operations=effective,
            no_change_reason=envelope.no_change_reason,
            evidence=top_evidence,
            source_references=top_references,
        )
        existing = session.scalar(
            select(Proposal).where(Proposal.request_id == request_id)
        )
        if existing is not None:
            if (
                existing.project_id != project_id
                or existing.fingerprint != fingerprint
                or existing.proposal_contract_version != "2"
                or existing.intent_id != intent.id
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            session.add(
                IdempotencyRecord(
                    request_id=request_id,
                    project_id=project_id,
                    action="proposal.create",
                    response_json=pack_idempotent_response(
                        {"proposal_id": existing.id}, request_identity
                    ),
                )
            )
            return self._public_proposal(session, existing)

        if envelope.result_kind == "changes":
            semantic_fingerprint = self._guided_semantic_fingerprint(
                intent_id=intent.id,
                workflow_mode=intent.workflow_mode,
                scope_type=intent.scope_type,
                scope_id=intent.scope_id,
                operations=effective,
                evidence=top_evidence,
                source_references=top_references,
            )
            candidates = session.scalars(
                select(Proposal).where(
                    Proposal.project_id == project.id,
                    Proposal.status == "draft",
                    Proposal.proposal_contract_version == "2",
                    Proposal.base_semantic_revision == project.semantic_revision,
                    Proposal.workflow_mode == intent.workflow_mode,
                    Proposal.scope_type == intent.scope_type,
                    Proposal.scope_id == intent.scope_id,
                    Proposal.result_kind == "changes",
                )
            ).all()
            for candidate in candidates:
                candidate_intent = (
                    session.get(AgentIntent, candidate.intent_id)
                    if candidate.intent_id
                    else None
                )
                if (
                    candidate_intent is None
                    or candidate_intent.instructions != intent.instructions
                    or candidate_intent.allow_completion != intent.allow_completion
                    or candidate_intent.artifact_locators_json != intent.artifact_locators_json
                    or candidate_intent.planning_profile_version != intent.planning_profile_version
                ):
                    continue
                rows = session.scalars(
                    select(ProposalOperation).where(
                        ProposalOperation.proposal_id == candidate.id
                    )
                ).all()
                candidate_operations = [
                    Operation.model_validate(
                        _unpack_persisted_operation(row.operation_json)[0]
                    )
                    for row in rows
                ]
                candidate_semantic = self._guided_semantic_fingerprint(
                    intent_id=str(candidate.intent_id or ""),
                    workflow_mode=candidate.workflow_mode,
                    scope_type=candidate.scope_type,
                    scope_id=candidate.scope_id,
                    operations=candidate_operations,
                    evidence=json.loads(candidate.top_level_evidence_json or "[]"),
                    source_references=json.loads(
                        candidate.top_level_source_references_json or "[]"
                    ),
                )
                if candidate_semantic != semantic_fingerprint:
                    continue
                intent.consumed_proposal_id = candidate.id
                if intent.regenerates_proposal_id:
                    prior = session.get(Proposal, intent.regenerates_proposal_id)
                    if (
                        prior is not None
                        and prior.project_id == project.id
                        and prior.id != candidate.id
                    ):
                        prior.superseded_by_proposal_id = candidate.id
                        if prior.intent_id:
                            prior_intent = session.get(AgentIntent, prior.intent_id)
                            if prior_intent is not None:
                                prior_intent.superseded_by_intent_id = intent.id
                        if prior.status in {"draft", "conflict"}:
                            prior.status = "superseded"
                            prior.closed_at = utcnow()
                session.add(
                    IdempotencyRecord(
                        request_id=request_id,
                        project_id=project_id,
                        action="proposal.create",
                        response_json=pack_idempotent_response(
                            {"proposal_id": candidate.id}, request_identity
                        ),
                    )
                )
                session.add(
                    OutboxEvent(
                        project_id=project_id,
                        event_type="proposal.deduplicated",
                        payload_json=canonical_json(
                            {
                                "proposal_id": candidate.id,
                                "intent_id": intent.id,
                            }
                        ),
                    )
                )
                session.flush()
                return self._public_proposal(session, candidate)

        now = utcnow()
        proposal = Proposal(
            id=request_id,
            project_id=project_id,
            request_id=request_id,
            base_semantic_revision=envelope.base_semantic_revision,
            summary=envelope.summary,
            rationale=envelope.rationale,
            status=("draft" if envelope.result_kind == "changes" else "no_changes"),
            fingerprint=fingerprint,
            actor_label=envelope.actor_label,
            proposal_contract_version="2",
            intent_id=intent.id,
            workflow_mode=intent.workflow_mode,
            scope_type=intent.scope_type,
            scope_id=intent.scope_id,
            result_kind=envelope.result_kind,
            no_change_reason=envelope.no_change_reason or "",
            scan_summary_json=canonical_json(envelope.scan_summary.model_dump(mode="json")),
            top_level_evidence_json=canonical_json(top_evidence),
            top_level_source_references_json=canonical_json(top_references),
            fingerprint_version=2,
            regenerates_proposal_id=intent.regenerates_proposal_id,
            closed_at=(now if envelope.result_kind == "no_changes" else None),
        )
        session.add(proposal)
        for operation in effective:
            public_data = {
                key: value
                for key, value in operation.data.items()
                if not key.startswith("_")
            }
            stored_operation = operation.model_copy(update={"data": public_data})
            session.add(
                ProposalOperation(
                    id=str(operation.id),
                    proposal_id=proposal.id,
                    operation_type=operation.type,
                    operation_json=_pack_persisted_operation(
                        stored_operation, operation_diffs[str(operation.id)]
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
                    basis=operation.basis or "",
                    disposition="pending",
                )
            )
        intent.consumed_proposal_id = proposal.id
        if intent.regenerates_proposal_id:
            prior = session.get(Proposal, intent.regenerates_proposal_id)
            if prior is not None and prior.project_id == project.id:
                prior.superseded_by_proposal_id = proposal.id
                if prior.intent_id:
                    prior_intent = session.get(AgentIntent, prior.intent_id)
                    if prior_intent is not None:
                        prior_intent.superseded_by_intent_id = intent.id
                if prior.status in {"draft", "conflict"}:
                    prior.status = "superseded"
                    prior.closed_at = now
        project.last_proposal_at = now
        if envelope.result_kind == "no_changes":
            project.last_agent_check_at = now
        session.add(
            IdempotencyRecord(
                request_id=request_id,
                project_id=project_id,
                action="proposal.create",
                response_json=pack_idempotent_response(
                    {"proposal_id": proposal.id}, request_identity
                ),
            )
        )
        session.add(
            OutboxEvent(
                project_id=project_id,
                event_type=(
                    "proposal.created"
                    if envelope.result_kind == "changes"
                    else "proposal.no_changes"
                ),
                payload_json=canonical_json(
                    {
                        "proposal_id": proposal.id,
                        "workflow_mode": intent.workflow_mode,
                        "scope_type": intent.scope_type,
                        "scope_id": intent.scope_id,
                        "result_kind": envelope.result_kind,
                        "warnings": warnings,
                    }
                ),
            )
        )
        session.flush()
        return self._public_proposal(session, proposal)

    def create_proposal(self, session: Session, project_id: str, envelope: ProposalEnvelope) -> dict[str, Any]:
        if envelope.proposal_contract_version == "2":
            return self._create_guided_proposal(session, project_id, envelope)
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
        validate_legacy_agent_constraints(session, project, envelope.operations)
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
            target_conflict = _stale_target_conflict(
                session, project, payload.operations
            )
            if target_conflict is not None:
                raise target_conflict
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
        validate_agent_operations(
            payload.operations,
            allow_guided_user_instruction_completion=(
                original.proposal_contract_version == "2"
            ),
        )
        topological_operations(payload.operations)
        replacement_operations = self._remap_revision_operations(payload.operations)
        if original.proposal_contract_version == "2":
            if original.intent_id is None:
                raise DomainError(
                    500,
                    "proposal_intent_missing",
                    "Guided proposal has no bound intent",
                )
            intent = require_intent(
                session,
                project,
                original.intent_id,
                require_unconsumed=False,
            )
            envelope = ProposalEnvelope(
                request_id=payload.request_id,
                project_id=payload.project_id,
                base_semantic_revision=payload.base_semantic_revision,
                proposal_contract_version="2",
                intent_id=UUID(intent.id),
                result_kind="changes",
                summary=payload.summary,
                rationale=payload.rationale,
                actor_label=payload.actor_label,
                scan_summary=json.loads(original.scan_summary_json or "{}"),
                evidence=json.loads(original.top_level_evidence_json or "[]"),
                source_references=json.loads(
                    original.top_level_source_references_json or "[]"
                ),
                operations=replacement_operations,
            )
            top_evidence, top_references = validate_top_level_v2_evidence(
                session,
                project,
                intent,
                envelope,
            )
            _intent, replacement_operations, _warnings = prepare_guided_operations(
                session,
                project,
                envelope,
                require_bound_request=False,
                require_unconsumed=False,
            )
            envelope = envelope.model_copy(
                update={"operations": replacement_operations}
            )
            fingerprint = proposal_fingerprint_v2(
                intent_id=intent.id,
                workflow_mode=intent.workflow_mode,
                scope_type=intent.scope_type,
                scope_id=intent.scope_id,
                result_kind="changes",
                operations=replacement_operations,
                evidence=top_evidence,
                source_references=top_references,
            )
            proposal_metadata = {
                "proposal_contract_version": "2",
                "intent_id": intent.id,
                "workflow_mode": intent.workflow_mode,
                "scope_type": intent.scope_type,
                "scope_id": intent.scope_id,
                "result_kind": "changes",
                "scan_summary_json": canonical_json(
                    envelope.scan_summary.model_dump(mode="json")
                ),
                "top_level_evidence_json": canonical_json(top_evidence),
                "top_level_source_references_json": canonical_json(
                    top_references
                ),
                "fingerprint_version": 2,
                "regenerates_proposal_id": original.regenerates_proposal_id,
            }
        else:
            validate_legacy_agent_constraints(session, project, replacement_operations)
            envelope = ProposalEnvelope(
                request_id=payload.request_id,
                project_id=payload.project_id,
                base_semantic_revision=payload.base_semantic_revision,
                summary=payload.summary,
                rationale=payload.rationale,
                actor_label=payload.actor_label,
                operations=replacement_operations,
            )
            fingerprint = proposal_fingerprint(replacement_operations)
            proposal_metadata = {}
        ordered = topological_operations(replacement_operations)
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
            fingerprint=fingerprint,
            actor_label=payload.actor_label,
            **proposal_metadata,
        )
        session.add(replacement)
        for operation in replacement_operations:
            public_operation = operation.model_copy(
                update={
                    "data": {
                        key: value
                        for key, value in operation.data.items()
                        if not key.startswith("_")
                    }
                }
            )
            session.add(
                ProposalOperation(
                    id=str(operation.id),
                    proposal_id=replacement.id,
                    operation_type=operation.type,
                    operation_json=_pack_persisted_operation(
                        public_operation, operation_diffs[str(operation.id)]
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
                    basis=operation.basis or "",
                    disposition="pending",
                )
            )

        original.superseded_by_proposal_id = replacement.id
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
        """Return the complete legacy list for callers that do not request paging."""
        self._project(session, project_id)
        proposals = session.scalars(select(Proposal).where(Proposal.project_id == project_id).order_by(Proposal.created_at.desc())).all()
        return [self._public_proposal(session, proposal) for proposal in proposals]

    def proposal_page(
        self,
        session: Session,
        project_id: str,
        *,
        summary: bool,
        status: str | None,
        workflow_mode: str | None,
        scope_type: str | None,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        """Return a stable cursor page without loading operation bodies in summary mode."""

        self._project(session, project_id)
        if status is not None and status not in PROPOSAL_STATUS_FILTERS:
            raise DomainError(
                422,
                "invalid_proposal_status_filter",
                "Unknown proposal status filter",
                {"status": status, "allowed": sorted(PROPOSAL_STATUS_FILTERS)},
            )
        if scope_type is not None and scope_type not in PROPOSAL_SCOPE_TYPES:
            raise DomainError(
                422,
                "invalid_proposal_scope_filter",
                "Unknown proposal scope filter",
                {"scope_type": scope_type, "allowed": sorted(PROPOSAL_SCOPE_TYPES)},
            )
        if workflow_mode is not None and not workflow_mode.strip():
            raise DomainError(
                422,
                "invalid_proposal_mode_filter",
                "Proposal workflow mode filter cannot be empty",
            )

        facet_conditions = [Proposal.project_id == project_id]
        if workflow_mode is not None:
            facet_conditions.append(Proposal.workflow_mode == workflow_mode)
        if scope_type is not None:
            facet_conditions.append(Proposal.scope_type == scope_type)

        conditions = list(facet_conditions)
        if status == "open":
            conditions.append(Proposal.status == "draft")
        elif status == "closed":
            conditions.append(Proposal.status != "draft")
        elif status is not None:
            conditions.append(Proposal.status == status)

        total = int(
            session.scalar(
                select(func.count()).select_from(Proposal).where(*conditions)
            )
            or 0
        )
        status_counts = {
            str(name): int(count)
            for name, count in session.execute(
                select(Proposal.status, func.count())
                .where(*facet_conditions)
                .group_by(Proposal.status)
                .order_by(Proposal.status)
            ).all()
        }
        result_kind_counts = {
            str(name): int(count)
            for name, count in session.execute(
                select(Proposal.result_kind, func.count())
                .where(*conditions)
                .group_by(Proposal.result_kind)
                .order_by(Proposal.result_kind)
            ).all()
        }
        workflow_mode_counts = {
            str(name): int(count)
            for name, count in session.execute(
                select(Proposal.workflow_mode, func.count())
                .where(Proposal.project_id == project_id)
                .group_by(Proposal.workflow_mode)
                .order_by(Proposal.workflow_mode)
            ).all()
        }

        signature = _proposal_page_signature(
            status=status, workflow_mode=workflow_mode, scope_type=scope_type
        )
        if cursor:
            cursor_time, cursor_id = _decode_proposal_cursor(cursor, signature)
            conditions.append(
                or_(
                    Proposal.created_at < cursor_time,
                    (
                        (Proposal.created_at == cursor_time)
                        & (Proposal.id < cursor_id)
                    ),
                )
            )
        statement = (
            select(Proposal)
            .where(*conditions)
            .order_by(Proposal.created_at.desc(), Proposal.id.desc())
            .limit(limit + 1)
        )
        rows = session.scalars(statement).all()
        has_more = len(rows) > limit
        page = rows[:limit]
        summary_rows_by_proposal: dict[str, list[ProposalOperation]] = {}
        summary_links: dict[str, dict[str, str | None]] = {}
        if summary and page:
            page_ids = [proposal.id for proposal in page]
            page_operation_rows = session.scalars(
                select(ProposalOperation)
                .where(ProposalOperation.proposal_id.in_(page_ids))
                .order_by(ProposalOperation.proposal_id, ProposalOperation.id)
            ).all()
            for operation_row in page_operation_rows:
                summary_rows_by_proposal.setdefault(operation_row.proposal_id, []).append(operation_row)
            summary_links = self._proposal_supersession_links_many(session, project_id, page_ids)
        public = [
            (
                self._public_proposal_summary(
                    session, proposal,
                    rows=summary_rows_by_proposal.get(proposal.id, []),
                    links=summary_links.get(proposal.id),
                )
                if summary
                else self._public_proposal(session, proposal)
            )
            for proposal in page
        ]
        next_cursor = (
            _encode_proposal_cursor(page[-1], signature)
            if has_more and page
            else None
        )
        draft_count = status_counts.get("draft", 0)
        return {
            "proposals": public,
            "count": len(public),
            "total": total,
            "limit": limit,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "summary": summary,
            "draft_count": draft_count,
            "closed_count": sum(status_counts.values()) - draft_count,
            "status_counts": status_counts,
            "result_kind_counts": result_kind_counts,
            "workflow_mode_counts": workflow_mode_counts,
        }

    @staticmethod
    def _public_proposal_summary(
        session: Session,
        proposal: Proposal,
        *,
        rows: list[ProposalOperation] | None = None,
        links: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        if rows is None:
            rows = session.scalars(
                select(ProposalOperation).where(
                    ProposalOperation.proposal_id == proposal.id
                )
            ).all()
        risk_counts = {"normal": 0, "high": 0}
        basis_counts: dict[str, int] = {}
        for row in rows:
            operation, _diff = _unpack_persisted_operation(row.operation_json)
            data = operation.get("data") or {}
            high_risk = _is_high_risk_operation(operation)
            risk_counts["high" if high_risk else "normal"] += 1
            basis = str(row.basis or operation.get("basis") or "legacy_unspecified")
            basis_counts[basis] = basis_counts.get(basis, 0) + 1
        if links is None:
            links = AppService._proposal_supersession_links(session, proposal)
        superseded_by = proposal.superseded_by_proposal_id or links["superseded_by_proposal_id"]
        return {
            "id": proposal.id,
            "project_id": proposal.project_id,
            "summary": proposal.summary,
            "rationale": proposal.rationale,
            "status": proposal.status,
            "base_semantic_revision": proposal.base_semantic_revision,
            "operations": [],
            "operation_count": len(rows),
            "detail_loaded": False,
            "detail_url": f"/api/v1/proposals/{proposal.id}",
            "created_at": jsonable(proposal.created_at),
            "closed_at": jsonable(proposal.closed_at),
            "actor_label": proposal.actor_label,
            "rejection_reason": proposal.rejection_reason,
            "proposal_contract_version": proposal.proposal_contract_version,
            "intent_id": proposal.intent_id,
            "workflow_mode": proposal.workflow_mode,
            "scope_type": proposal.scope_type,
            "scope_id": proposal.scope_id,
            "result_kind": proposal.result_kind,
            "no_change_reason": proposal.no_change_reason or None,
            "scan_summary": json.loads(proposal.scan_summary_json or "{}"),
            "fingerprint_version": proposal.fingerprint_version,
            "regenerates_proposal_id": proposal.regenerates_proposal_id,
            "supersedes_proposal_id": links["supersedes_proposal_id"],
            "superseded_by_proposal_id": superseded_by,
            "risk_counts": risk_counts,
            "basis_counts": dict(sorted(basis_counts.items())),
            "evidence_count": len(json.loads(proposal.top_level_evidence_json or "[]")),
            "source_reference_count": len(
                json.loads(proposal.top_level_source_references_json or "[]")
            ),
        }

    def proposal(self, session: Session, proposal_id: str) -> dict[str, Any]:
        proposal = session.get(Proposal, proposal_id)
        if proposal is None:
            raise DomainError(404, "proposal_not_found", "Proposal not found")
        return self._public_proposal(session, proposal)

    @staticmethod
    def _proposal_supersession_links_many(
        session: Session, project_id: str, proposal_ids: list[str]
    ) -> dict[str, dict[str, str | None]]:
        identifiers = set(proposal_ids)
        result = {
            proposal_id: {
                "supersedes_proposal_id": None,
                "superseded_by_proposal_id": None,
            }
            for proposal_id in identifiers
        }
        if not identifiers:
            return result
        replacement_field = func.json_extract(OutboxEvent.payload_json, "$.replacement_proposal_id")
        superseded_field = func.json_extract(OutboxEvent.payload_json, "$.superseded_proposal_id")
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.project_id == project_id,
                OutboxEvent.event_type == "proposal.superseded",
                or_(
                    replacement_field.in_(identifiers),
                    superseded_field.in_(identifiers),
                ),
            ).order_by(OutboxEvent.id.desc()).limit(max(1, len(identifiers) * 4))
        ).all()
        for event in events:
            try:
                payload = json.loads(event.payload_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            replacement_id = str(payload.get("replacement_proposal_id") or "")
            superseded_id = str(payload.get("superseded_proposal_id") or "")
            if replacement_id in result:
                result[replacement_id]["supersedes_proposal_id"] = superseded_id or None
            if superseded_id in result:
                result[superseded_id]["superseded_by_proposal_id"] = replacement_id or None
        return result
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
        replacements: dict[str, str] = {}
        if proposal.intent_id:
            intent = session.get(AgentIntent, proposal.intent_id)
            if intent is not None:
                for raw in json.loads(intent.artifact_locators_json or "[]"):
                    public = _redacted_explicit_locator(session, proposal.project_id, raw)
                    if public.get("redacted"):
                        replacements[str(raw.get("locator") or "")] = str(public["locator"])

        def redact(value: Any) -> Any:
            if isinstance(value, str):
                return replacements.get(value, value)
            if isinstance(value, list):
                return [redact(item) for item in value]
            if isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            return value

        for row in rows:
            operation, diff = _unpack_persisted_operation(row.operation_json)
            if diff is not None:
                operation["before"] = diff["before"]
                operation["after"] = diff["after"]
            operation = redact(operation)
            operation["disposition"] = row.disposition
            operation["basis"] = row.basis or operation.get("basis")
            high_risk = _is_high_risk_operation(operation)
            operation["risk"] = "high" if high_risk else "normal"
            operation["default_selected"] = bool(
                proposal.proposal_contract_version == "2"
                and operation.get("basis") != "inference"
                and not high_risk
            )
            operations.append(operation)
        selected_defaults = (
            _closure_safe_default_selection(operations)
            if proposal.proposal_contract_version == "2"
            else set()
        )
        for operation in operations:
            operation["default_selected"] = str(operation.get("id")) in selected_defaults
        links = AppService._proposal_supersession_links(session, proposal)
        superseded_by = proposal.superseded_by_proposal_id or links["superseded_by_proposal_id"]
        return {
            "id": proposal.id, "project_id": proposal.project_id, "summary": proposal.summary,
            "rationale": proposal.rationale, "status": proposal.status,
            "base_semantic_revision": proposal.base_semantic_revision,
            "operations": operations, "created_at": jsonable(proposal.created_at),
            "closed_at": jsonable(proposal.closed_at), "actor_label": proposal.actor_label,
            "rejection_reason": proposal.rejection_reason,
            "proposal_contract_version": proposal.proposal_contract_version,
            "intent_id": proposal.intent_id,
            "workflow_mode": proposal.workflow_mode,
            "scope_type": proposal.scope_type,
            "scope_id": proposal.scope_id,
            "result_kind": proposal.result_kind,
            "no_change_reason": proposal.no_change_reason or None,
            "scan_summary": json.loads(proposal.scan_summary_json or "{}"),
            "evidence": json.loads(proposal.top_level_evidence_json or "[]"),
            "source_references": json.loads(
                proposal.top_level_source_references_json or "[]"
            ),
            "fingerprint_version": proposal.fingerprint_version,
            "regenerates_proposal_id": proposal.regenerates_proposal_id,
            "supersedes_proposal_id": links["supersedes_proposal_id"],
            "superseded_by_proposal_id": superseded_by,
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
                if isinstance(response, dict) and isinstance(response.get("code"), str):
                    raise DomainError(
                        409,
                        str(response["code"]),
                        str(
                            response.get("message")
                            or "Proposal is stale and must be regenerated"
                        ),
                        response.get("details"),
                    )
                # Compatibility with conflict idempotency records written before
                # target-specific stale errors were persisted.
                raise DomainError(
                    409,
                    "revision_conflict",
                    "Proposal is stale and must be regenerated",
                    response,
                )
            if duplicate.action != "mutation":
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            return response
        proposal = session.get(Proposal, proposal_id)
        if proposal is None or proposal.project_id != project_id:
            raise DomainError(404, "proposal_not_found", "Proposal not found")
        if proposal.status != "draft":
            raise DomainError(409, "proposal_closed", "Proposal is no longer open")
        project = self._project(session, project_id)
        rows = session.scalars(
            select(ProposalOperation).where(
                ProposalOperation.proposal_id == proposal.id
            )
        ).all()
        by_id = {row.id: row for row in rows}
        selected = {str(value) for value in payload.selected_operation_ids}
        unknown = selected - set(by_id)
        if proposal.base_semantic_revision != project.semantic_revision:
            target_conflict = None
            if not unknown:
                selected_operations = [
                    Operation.model_validate(
                        _unpack_persisted_operation(by_id[row_id].operation_json)[0]
                    )
                    for row_id in selected
                ]
                target_conflict = _stale_target_conflict(
                    session, project, selected_operations
                )
            error = target_conflict or DomainError(
                409,
                "revision_conflict",
                "Proposal is stale and must be regenerated",
            )
            detail = {
                **(
                    error.details
                    if isinstance(error.details, dict)
                    else ({"conflict_details": error.details} if error.details else {})
                ),
                "proposal_revision": proposal.base_semantic_revision,
                "current": project.semantic_revision,
            }
            error = DomainError(409, error.code, error.message, detail)
            self._mark_proposal_conflict(
                session, proposal, {"conflict_code": error.code, **detail}
            )
            session.add(
                IdempotencyRecord(
                    request_id=request_id,
                    project_id=project_id,
                    action="proposal.apply.conflict",
                    response_json=pack_idempotent_response(
                        error.as_detail(), apply_identity
                    ),
                )
            )
            session.flush()
            raise error
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
        if proposal.proposal_contract_version == "2" and overrides:
            if not proposal.intent_id:
                raise DomainError(500, "proposal_intent_missing", "Guided proposal has no bound intent")
            bound_intent = session.get(AgentIntent, proposal.intent_id)
            if bound_intent is None:
                raise DomainError(500, "proposal_intent_missing", "Guided proposal intent is unavailable")
            overrides = {
                operation_id: _resolve_intent_locator(bound_intent, operation)
                for operation_id, operation in overrides.items()
            }
        unknown_overrides = set(overrides) - selected
        if unknown_overrides:
            raise DomainError(422, "invalid_operation_override", "Only selected proposal operations may be edited", sorted(unknown_overrides))
        reference_fields = (
            "pipeline_id", "parent_id", "task_id", "artifact_id",
            "source_task_id", "source_id", "target_task_id", "target_id",
        )

        def target_identity(operation: Operation) -> tuple[Any, ...]:
            values = tuple(operation.data.get(field) for field in reference_fields)
            if operation.type == "artifact.create":
                return values + (str(operation.data.get("kind") or "local"), operation.data.get("artifact_root_id"), operation.data.get("locator"))
            if operation.type == "edge.create":
                return values + (str(operation.data.get("edge_type") or "dependency"),)
            return values

        for operation_id, override in overrides.items():
            original = persisted[operation_id]
            immutable_original = (
                original.type, original.resolved_entity_id(), original.atomic_group_id,
                tuple(original.prerequisite_operation_ids),
                original.basis,
                tuple(sorted(
                    str(item.get("kind") or "")
                    for item in original.evidence if isinstance(item, dict)
                )),
                target_identity(original),
            )
            immutable_override = (
                override.type, override.resolved_entity_id(), override.atomic_group_id,
                tuple(override.prerequisite_operation_ids),
                override.basis,
                tuple(sorted(
                    str(item.get("kind") or "")
                    for item in override.evidence if isinstance(item, dict)
                )),
                target_identity(override),
            )
            if immutable_override != immutable_original:
                raise DomainError(
                    422, "immutable_operation_identity",
                    "Editing cannot change operation type, target, evidence class, basis, atomic group, or prerequisites",
                    {"operation_id": operation_id},
                )
        effective = [overrides.get(row.id, persisted[row.id]) for row in rows if row.id in selected]
        if proposal.proposal_contract_version == "2":
            guided_envelope = ProposalEnvelope(
                request_id=payload.request_id,
                project_id=UUID(project_id),
                base_semantic_revision=project.semantic_revision,
                proposal_contract_version="2",
                intent_id=UUID(str(proposal.intent_id)),
                result_kind="changes",
                summary=proposal.summary,
                rationale=proposal.rationale,
                actor_label=proposal.actor_label,
                scan_summary=json.loads(proposal.scan_summary_json or "{}"),
                evidence=json.loads(proposal.top_level_evidence_json or "[]"),
                source_references=json.loads(
                    proposal.top_level_source_references_json or "[]"
                ),
                operations=effective,
            )
            _intent, effective, _warnings = prepare_guided_operations(
                session, project, guided_envelope,
                require_bound_request=False, require_unconsumed=False,
            )
        else:
            validate_agent_operations(effective)
            validate_legacy_agent_constraints(session, project, effective)
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
                applied_operation = next(
                    item for item in operations if str(item.id) == row.id
                )
                public_operation = applied_operation.model_copy(
                    update={
                        "data": {
                            key: value
                            for key, value in applied_operation.data.items()
                            if not key.startswith("_")
                        }
                    }
                )
                row.operation_json = _pack_persisted_operation(
                    public_operation,
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
                row.basis = override.basis or ""
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

    @staticmethod
    def _export_root_aliases(
        roots: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Create stable, unique, path-free aliases for portable exports."""

        aliases: dict[str, str] = {}
        used: set[str] = set()
        ordered = sorted(
            roots,
            key=lambda root: (
                not bool(root["is_project_root"]),
                str(root["name"]).casefold(),
                str(root["id"]),
            ),
        )
        for root in ordered:
            base = (
                "project"
                if root["is_project_root"]
                else str(root["name"]).strip() or "artifact-root"
            )
            candidate = base
            if candidate.casefold() in used:
                suffix = str(root["id"])[:8]
                candidate = f"{base}~{suffix}"
                index = 2
                while candidate.casefold() in used:
                    candidate = f"{base}~{suffix}-{index}"
                    index += 1
            used.add(candidate.casefold())
            aliases[str(root["id"])] = candidate
        return aliases

    def export_project(self, session: Session, project_id: str) -> dict[str, Any]:
        snapshot = self.snapshot(session, project_id)
        project = dict(snapshot["project"])
        project["root_path"] = None
        project.pop("availability", None)
        project.pop("unavailable", None)
        snapshot["project"] = project

        raw_roots = list(snapshot["artifact_roots"])
        aliases = self._export_root_aliases(raw_roots)
        project_root_alias = next(
            (aliases[root["id"]] for root in raw_roots if root["is_project_root"]),
            "project",
        )
        snapshot["artifact_roots"] = [
            {
                "alias": aliases[root["id"]],
                "name": root["name"],
                "is_project_root": root["is_project_root"],
            }
            for root in raw_roots
        ]

        scan_policy = dict(snapshot["scan_policy"])
        readable_root_ids = scan_policy.pop("readable_source_root_ids", []) or []
        scan_policy["readable_source_root_aliases"] = sorted(
            aliases.get(str(root_id), f"unavailable:{root_id}")
            for root_id in readable_root_ids
        )
        scan_policy["allow_outside_sources"] = bool(readable_root_ids)
        snapshot["scan_policy"] = scan_policy

        for artifact in snapshot["artifacts"]:
            root_id = artifact.pop("artifact_root_id", None)
            artifact["artifact_root_alias"] = aliases.get(
                root_id, f"unavailable:{root_id}" if root_id else None
            )
            artifact.pop("available", None)
            artifact.pop("mime_type", None)
            artifact.pop("size_bytes", None)
            artifact.pop("validation_warning", None)

        source_references = session.scalars(
            select(SourceReference)
            .where(SourceReference.project_id == project_id)
            .order_by(SourceReference.id)
        ).all()
        snapshot["source_references"] = []
        for reference in source_references:
            value = model_dict(reference, exclude={"project_id"})
            source_root_id = value.pop("source_root_id", None)
            value["source_root_alias"] = aliases.get(
                source_root_id,
                project_root_alias
                if source_root_id is None
                else f"unavailable:{source_root_id}",
            )
            snapshot["source_references"].append(value)

        task_source_references = session.scalars(
            select(TaskSourceReference)
            .where(TaskSourceReference.project_id == project_id)
            .order_by(TaskSourceReference.id)
        ).all()
        snapshot["task_source_references"] = [
            model_dict(item, exclude={"project_id", "created_at"})
            for item in task_source_references
        ]

        for collection in (
            "artifact_roots", "pipelines", "tasks", "edges", "journals",
            "artifacts", "task_artifacts", "layouts", "viewports",
            "source_references", "task_source_references",
        ):
            values = snapshot.get(collection, [])
            snapshot[collection] = sorted(values, key=canonical_json)

        return {
            "schema_version": "1",
            "export_contract_version": "2",
            "export_kind": "research-monitor-project",
            "project": snapshot,
        }
