from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .graph import GraphCycleError, descendants, validate_dag
from .models import (
    GraphViewport,
    Artifact, ArtifactRoot, AuditEvent, IdempotencyRecord, JournalEntry, OutboxEvent,
    Pipeline, PlanningProfile, Project, ScanPolicy, SourceReference, Task, TaskArtifact, TaskEdge,
    TaskLayout, utcnow,
)
from .schemas import LayoutMutationEnvelope, MutationEnvelope, MutationUndo, Operation
from .serializers import (
    canonical_json, model_dict, pack_idempotent_response, request_fingerprint,
    unpack_idempotent_response,
)
from .service import (
    ARTIFACT_ROLES, EDGE_TYPES, FLOW_MODES, JOURNAL_TYPES, TASK_KINDS, TASK_OUTCOMES,
    TASK_PRIORITIES, TASK_STATUSES, DomainError, ResearchMonitorService,
    _artifact_path, _public_artifact, _public_artifact_root, _public_edge,
    _public_pipeline, _public_planning_profile, _public_project, _public_scan_policy, _public_task,
    _uuid, _validate_monitor_storage_separation, _validated_directory,
)
from .url_safety import parse_http_url


SEMANTIC_OPERATION_TYPES = {
    "project.update", "project.archive", "project.trash", "project.restore", "project.relink",
    "planning_profile.update",
    "scan_policy.update", "pipeline.create", "pipeline.update", "pipeline.archive",
    "pipeline.delete", "pipeline.restore", "task.create", "task.update", "task.move",
    "task.delete", "task.restore", "edge.create", "edge.update", "edge.delete",
    "journal.create", "journal.update", "journal.delete", "artifact_root.create",
    "artifact_root.delete", "artifact.create", "artifact.update", "artifact.delete",
    "task_artifact.link", "task_artifact.unlink",
}
LAYOUT_OPERATION_TYPES = {"layout.upsert", "layout.delete", "viewport.upsert"}
OPERATION_UNIQUE_CONSTRAINT_TABLES = {
    "pipelines", "tasks", "task_edges", "journal_entries", "artifact_roots",
    "artifacts", "task_artifacts", "task_layouts", "graph_viewports", "proposal_operations",
    "planning_profiles",
}


def operation_integrity_error(
    exc: IntegrityError,
    operation: Operation | None = None,
) -> DomainError | None:
    """Translate only recognized user-controlled unique-key conflicts.

    Foreign-key failures and constraints on internal bookkeeping tables remain
    unhandled because those indicate an invariant or implementation defect, not
    malformed operation data.
    """
    message = str(getattr(exc, "orig", exc))
    if "UNIQUE constraint failed:" not in message:
        return None
    if not any(f"{table}." in message for table in OPERATION_UNIQUE_CONSTRAINT_TABLES):
        return None
    details: dict[str, Any] = {}
    if operation is not None:
        details.update(
            operation_id=str(operation.id),
            operation_type=operation.type,
        )
    return DomainError(
        422,
        "operation_integrity_error",
        "Operation reuses an identity or unique value that already exists",
        details or None,
    )


class MutationService(ResearchMonitorService):
    @staticmethod
    def semantic_operation_types() -> set[str]:
        return set(SEMANTIC_OPERATION_TYPES)

    @staticmethod
    def layout_operation_types() -> set[str]:
        return set(LAYOUT_OPERATION_TYPES)

    def mutate(self, session: Session, envelope: MutationEnvelope) -> dict[str, Any]:
        return self._mutate(session, envelope)

    def _mutate(
        self,
        session: Session,
        envelope: MutationEnvelope,
        *,
        idempotency_action: str = "mutation",
        response_extra: dict[str, Any] | None = None,
        idempotency_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        project = self._project(session, str(envelope.project_id))
        request_id = str(envelope.request_id)
        request_identity = idempotency_fingerprint or request_fingerprint(
            {
                "action": idempotency_action,
                "project_id": project.id,
                "payload": envelope.model_dump(mode="json"),
            }
        )
        duplicate = session.get(IdempotencyRecord, request_id)
        if duplicate is not None:
            response, stored_fingerprint = unpack_idempotent_response(duplicate.response_json)
            if (
                duplicate.project_id != project.id
                or duplicate.action != idempotency_action
                or (stored_fingerprint is not None and stored_fingerprint != request_identity)
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            return response
        if project.semantic_revision != envelope.base_semantic_revision:
            raise DomainError(409, "revision_conflict", "Project changed since this edit was prepared", {"expected": envelope.base_semantic_revision, "current": project.semantic_revision})
        invalid = sorted({op.type for op in envelope.operations} - SEMANTIC_OPERATION_TYPES)
        if invalid:
            raise DomainError(422, "unknown_operation", "Unsupported semantic operation", invalid)
        results = []
        entity_aliases: dict[str, str] = {}
        for operation in envelope.operations:
            try:
                resolved_operation = self._resolve_operation_aliases(operation, entity_aliases)
                results.append(
                    self._apply_operation(
                        session,
                        project,
                        resolved_operation,
                        envelope.actor_type,
                        envelope.actor_label,
                        request_id,
                        entity_aliases,
                    )
                )
            except IntegrityError as exc:
                translated = operation_integrity_error(exc, operation)
                if translated is None:
                    raise
                raise translated from exc
        self._validate_project(session, project.id)
        semantic_changed = any(
            bool(result.get("changed", True)) for result in results
        )
        if semantic_changed:
            project.semantic_revision += 1
            project.entity_version += 1
            project.updated_at = utcnow()
            if envelope.actor_type == "ui":
                project.last_manual_update_at = utcnow()
        session.flush()
        response = {
            "request_id": request_id,
            "project_id": project.id,
            "semantic_revision": project.semantic_revision,
            "layout_revision": project.layout_revision,
            "semantic_changed": semantic_changed,
            "results": results,
        }
        response.update(response_extra or {})
        session.add(IdempotencyRecord(request_id=request_id, project_id=project.id, action=idempotency_action, response_json=pack_idempotent_response(response, request_identity)))
        session.flush()
        return response

    def undo(
        self,
        session: Session,
        project_id: str,
        target_request_id: str,
        payload: MutationUndo,
    ) -> dict[str, Any]:
        project = self._project(session, project_id)
        request_id = str(payload.request_id)
        target_request_id = str(target_request_id)
        undo_identity = request_fingerprint(
            {
                "action": "undo",
                "project_id": project.id,
                "target_request_id": target_request_id,
                "payload": payload.model_dump(mode="json"),
            }
        )
        duplicate = session.get(IdempotencyRecord, request_id)
        if duplicate is not None:
            response, stored_fingerprint = unpack_idempotent_response(duplicate.response_json)
            if (
                duplicate.project_id != project.id
                or duplicate.action != "undo"
                or (stored_fingerprint is not None and stored_fingerprint != undo_identity)
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            return response
        if project.semantic_revision != payload.base_semantic_revision:
            raise DomainError(
                409,
                "revision_conflict",
                "Project changed since this undo was prepared",
                {"expected": payload.base_semantic_revision, "current": project.semantic_revision},
            )
        operations = self._build_undo_operations(session, project, target_request_id)
        envelope = MutationEnvelope(
            request_id=payload.request_id,
            project_id=project.id,
            base_semantic_revision=payload.base_semantic_revision,
            actor_type="ui",
            actor_label=f"Undo of {target_request_id[:8]}",
            operations=operations,
        )
        return self._mutate(
            session,
            envelope,
            idempotency_action="undo",
            response_extra={"undone_request_id": target_request_id},
            idempotency_fingerprint=undo_identity,
        )

    def undo_capability(self, session: Session, project_id: str, target_request_id: str) -> dict[str, Any]:
        project = self._project(session, project_id)
        try:
            operations = self._build_undo_operations(session, project, target_request_id)
        except DomainError as exc:
            return {"undoable": False, "undo_reason": exc.message, "undo_code": exc.code}
        return {"undoable": True, "undo_reason": None, "undo_code": None, "undo_operation_count": len(operations)}

    @staticmethod
    def _undo_error(message: str, code: str = "undo_not_available") -> DomainError:
        return DomainError(409, code, message)

    def _build_undo_operations(
        self,
        session: Session,
        project: Project,
        target_request_id: str,
    ) -> list[Operation]:
        events = session.scalars(
            select(AuditEvent)
            .where(AuditEvent.project_id == project.id, AuditEvent.request_id == target_request_id)
            .order_by(AuditEvent.sequence.desc())
        ).all()
        if not events:
            raise self._undo_error("Mutation request was not found", "undo_request_not_found")
        if any(event.actor_type != "ui" for event in events):
            raise self._undo_error("Only direct manual UI mutation requests can be undone")
        target_record = session.get(IdempotencyRecord, target_request_id)
        if target_record is not None and target_record.action == "undo":
            raise self._undo_error("Undo requests cannot themselves be undone in v1")
        for record in session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.project_id == project.id,
                IdempotencyRecord.action == "undo",
            )
        ):
            try:
                response, _fingerprint = unpack_idempotent_response(record.response_json)
                if response.get("undone_request_id") == target_request_id:
                    raise self._undo_error("This mutation request was already undone", "already_undone")
            except (TypeError, ValueError):
                continue

        entity_keys = [(event.entity_type, event.entity_id) for event in events]
        if len(entity_keys) != len(set(entity_keys)):
            raise self._undo_error("A request that changes the same entity more than once has no safe v1 inverse")
        created_keys = {
            (event.entity_type, event.entity_id)
            for event in events
            if json.loads(event.before_json) is None and json.loads(event.after_json) is not None
        }
        last_sequence = max(event.sequence for event in events)
        later_events = session.scalars(
            select(AuditEvent).where(
                AuditEvent.project_id == project.id,
                AuditEvent.sequence > last_sequence,
            )
        ).all()
        operations: list[Operation] = []
        for event in events:
            before = json.loads(event.before_json)
            after = json.loads(event.after_json)
            current = self._ensure_undo_entity_unchanged(session, project, event, after, later_events)
            operations.append(
                self._inverse_operation(
                    session,
                    project,
                    event,
                    before,
                    after,
                    current,
                    created_keys,
                    later_events,
                )
            )
        return operations

    def _ensure_undo_entity_unchanged(
        self,
        session: Session,
        project: Project,
        event: AuditEvent,
        after: Any,
        later_events: list[AuditEvent],
    ) -> Any:
        models: dict[str, type] = {
            "project": Project,
            "planning_profile": PlanningProfile,
            "scan_policy": ScanPolicy,
            "pipeline": Pipeline,
            "task": Task,
            "edge": TaskEdge,
            "journal": JournalEntry,
            "artifact_root": ArtifactRoot,
            "artifact": Artifact,
            "task_artifact": TaskArtifact,
        }
        model = models.get(event.entity_type)
        if model is None:
            raise self._undo_error(f"{event.action} has no safe v1 inverse")
        lookup_id = project.id if event.entity_type in {"scan_policy", "planning_profile"} else event.entity_id
        current = session.get(model, lookup_id)
        changed_later = any(
            row.entity_type == event.entity_type and row.entity_id == event.entity_id
            for row in later_events
        )
        if changed_later:
            raise self._undo_error("An entity touched by this request changed afterward", "undo_conflict")
        if after is None:
            if current is not None:
                raise self._undo_error("An entity removed by this request was recreated afterward", "undo_conflict")
            return None
        if current is None:
            raise self._undo_error("An entity touched by this request is no longer available", "undo_conflict")
        if event.entity_type != "project" and hasattr(current, "entity_version"):
            recorded_version = after.get("version", after.get("entity_version")) if isinstance(after, dict) else None
            if recorded_version is None or current.entity_version != recorded_version:
                raise self._undo_error("An entity touched by this request changed afterward", "undo_conflict")
        return current

    def _inverse_operation(
        self,
        session: Session,
        project: Project,
        event: AuditEvent,
        before: Any,
        after: Any,
        current: Any,
        created_keys: set[tuple[str, str]],
        later_events: list[AuditEvent],
    ) -> Operation:
        action = event.action
        entity_id = event.entity_id
        expected_version = getattr(current, "entity_version", None) if current is not None else None

        if action == "project.update":
            keys = ("name", "description", "research_goal", "success_criteria", "color")
            current_value = _public_project(project)
            if any(current_value.get(key) != after.get(key) for key in keys):
                raise self._undo_error("Project details changed after this request", "undo_conflict")
            return Operation(type=action, entity_id=entity_id, expected_version=project.entity_version, data={key: before.get(key) for key in keys})
        if action == "scan_policy.update":
            keys = (
                "preferred_sources", "include_globs", "exclude_globs", "sensitive_patterns",
                "max_text_file_size", "git_history_limit", "allow_git_metadata",
                "readable_source_root_ids", "max_files_per_scan", "max_total_text_bytes",
            )
            return Operation(type=action, entity_id=entity_id, expected_version=expected_version, data={key: before.get(key) for key in keys})
        if action == "planning_profile.update":
            keys = (
                "task_granularity", "max_nesting_depth", "planning_horizon",
                "inference_policy", "max_new_tasks_per_proposal",
                "preferred_pipeline_names", "terminology_notes",
                "additional_instructions", "protected_pipeline_ids",
                "protected_task_ids",
            )
            return Operation(
                type=action,
                entity_id=entity_id,
                expected_version=expected_version,
                data={key: before.get(key) for key in keys},
            )
        if action == "pipeline.update":
            keys = ("title", "description", "flow_mode", "position")
            return Operation(type=action, entity_id=entity_id, expected_version=expected_version, data={key: before.get(key) for key in keys})
        if action == "pipeline.archive":
            if before.get("archived") or not after.get("archived") or before.get("deleted_at") != after.get("deleted_at"):
                raise self._undo_error("Pipeline lifecycle transition has no exact v1 inverse")
            return Operation(type="pipeline.restore", entity_id=entity_id, expected_version=expected_version)
        if action == "task.update":
            unsafe_fields = (
                "pipeline_id", "parent_id", "position", "deleted_at", "completed_at",
                "completion_actor", "completion_source", "completion_provenance",
            )
            if before.get("status") == "done" or after.get("status") == "done" or any(before.get(key) != after.get(key) for key in unsafe_fields):
                raise self._undo_error("Completion and structural task changes have no exact v1 inverse")
            keys = (
                "user_key", "kind", "title", "description", "status", "outcome", "priority",
                "labels", "target_date", "completion_criteria", "blocker_reason",
                "completion_summary", "completion_override_reason", "child_flow_mode",
            )
            changed_keys = [key for key in keys if before.get(key) != after.get(key)]
            if not changed_keys:
                raise self._undo_error("This task edit has no reversible semantic difference")
            if any(key in {"user_key", "target_date"} and before.get(key) is None for key in changed_keys):
                raise self._undo_error("A nullable task field cannot be reconstructed exactly by the v1 mutation contract")
            return Operation(type=action, entity_id=entity_id, expected_version=expected_version, data={key: before.get(key) for key in changed_keys})
        if action == "edge.update":
            if before.get("disabled_reason") not in {None, "manual"} or after.get("disabled_reason") not in {None, "manual"}:
                raise self._undo_error("System-disabled dependency state has no exact v1 inverse")
            return Operation(
                type=action,
                entity_id=entity_id,
                expected_version=expected_version,
                data={
                    "edge_type": before.get("edge_type"),
                    "waiver_reason": before.get("waiver_reason") or "",
                    "disabled": bool(before.get("disabled")),
                },
            )
        if action == "journal.update":
            keys = ("content", "entry_type", "occurred_at")
            return Operation(type=action, entity_id=entity_id, expected_version=expected_version, data={key: before.get(key) for key in keys})
        if action == "artifact.update":
            keys = ("kind", "artifact_root_id", "locator", "provider", "label", "notes")
            return Operation(type=action, entity_id=entity_id, expected_version=expected_version, data={key: before.get(key) for key in keys})

        create_inverse = {
            "pipeline.create": ("pipeline.delete", {"cascade": False}),
            "task.create": ("task.delete", {}),
            "edge.create": ("edge.delete", {}),
            "journal.create": ("journal.delete", {}),
            "artifact_root.create": ("artifact_root.delete", {}),
            "artifact.create": ("artifact.delete", {}),
            "task_artifact.link": ("task_artifact.unlink", {}),
        }
        if action in create_inverse:
            self._guard_create_inverse(
                session, event.entity_type, entity_id, created_keys, later_events
            )
            inverse_type, data = create_inverse[action]
            return Operation(type=inverse_type, entity_id=entity_id, expected_version=expected_version, data=data)
        if action == "task_artifact.unlink":
            if not isinstance(before, dict):
                raise self._undo_error("Removed task-artifact association cannot be reconstructed")
            return Operation(
                type="task_artifact.link",
                entity_id=entity_id,
                data={key: before.get(key) for key in ("task_id", "artifact_id", "role", "notes")},
            )
        raise self._undo_error(f"{action} has no safe v1 inverse")

    def _guard_create_inverse(
        self,
        session: Session,
        entity_type: str,
        entity_id: str,
        created_keys: set[tuple[str, str]],
        later_events: list[AuditEvent],
    ) -> None:
        dependencies: list[tuple[str, str]] = []
        if entity_type == "pipeline":
            dependencies.extend(("task", item.id) for item in session.scalars(select(Task).where(Task.pipeline_id == entity_id)))
        elif entity_type == "task":
            dependencies.extend(("task", item.id) for item in session.scalars(select(Task).where(Task.parent_id == entity_id)))
            dependencies.extend(("edge", item.id) for item in session.scalars(select(TaskEdge).where((TaskEdge.source_id == entity_id) | (TaskEdge.target_id == entity_id))))
            dependencies.extend(("journal", item.id) for item in session.scalars(select(JournalEntry).where(JournalEntry.task_id == entity_id)))
            dependencies.extend(("task_artifact", item.id) for item in session.scalars(select(TaskArtifact).where(TaskArtifact.task_id == entity_id)))
            dependencies.extend(("source_reference", item.id) for item in session.scalars(select(SourceReference).where(SourceReference.task_id == entity_id)))
        elif entity_type == "artifact_root":
            dependencies.extend(("artifact", item.id) for item in session.scalars(select(Artifact).where(Artifact.root_id == entity_id)))
        elif entity_type == "artifact":
            dependencies.extend(("task_artifact", item.id) for item in session.scalars(select(TaskArtifact).where(TaskArtifact.artifact_id == entity_id)))
        external = [key for key in dependencies if key not in created_keys]
        if external:
            raise self._undo_error("A created entity gained dependent monitor records afterward", "undo_conflict")
        for row in later_events:
            try:
                values = (json.loads(row.before_json), json.loads(row.after_json))
            except (TypeError, ValueError):
                continue
            if any(self._json_mentions(value, entity_id) for value in values):
                raise self._undo_error("A created entity was referenced by a later semantic change", "undo_conflict")

    @classmethod
    def _json_mentions(cls, value: Any, needle: str) -> bool:
        if isinstance(value, dict):
            return any(cls._json_mentions(item, needle) for item in value.values())
        if isinstance(value, list):
            return any(cls._json_mentions(item, needle) for item in value)
        return value == needle

    def mutate_layout(self, session: Session, envelope: LayoutMutationEnvelope) -> dict[str, Any]:
        project = self._project(session, str(envelope.project_id))
        request_id = str(envelope.request_id)
        request_identity = request_fingerprint(
            {
                "action": "layout_mutation",
                "project_id": project.id,
                "payload": envelope.model_dump(mode="json"),
            }
        )
        duplicate = session.get(IdempotencyRecord, request_id)
        if duplicate is not None:
            response, stored_fingerprint = unpack_idempotent_response(duplicate.response_json)
            if (
                duplicate.project_id != project.id
                or duplicate.action != "layout_mutation"
                or (stored_fingerprint is not None and stored_fingerprint != request_identity)
            ):
                raise DomainError(409, "idempotency_collision", "Request ID was already used")
            return response
        if project.layout_revision != envelope.base_layout_revision:
            raise DomainError(409, "layout_revision_conflict", "Graph layout has changed", {"expected": envelope.base_layout_revision, "current": project.layout_revision})
        invalid = sorted({op.type for op in envelope.operations} - LAYOUT_OPERATION_TYPES)
        if invalid:
            raise DomainError(422, "unknown_operation", "Unsupported layout operation", invalid)
        results = []
        for operation in envelope.operations:
            try:
                results.append(self._apply_layout_operation(session, project, operation))
            except IntegrityError as exc:
                translated = operation_integrity_error(exc, operation)
                if translated is None:
                    raise
                raise translated from exc
        project.layout_revision += 1
        project.updated_at = utcnow()
        response = {"request_id": request_id, "project_id": project.id, "semantic_revision": project.semantic_revision, "layout_revision": project.layout_revision, "results": results}
        session.add(IdempotencyRecord(request_id=request_id, project_id=project.id, action="layout_mutation", response_json=pack_idempotent_response(response, request_identity)))
        session.add(OutboxEvent(project_id=project.id, event_type="layout.changed", payload_json=canonical_json(response)))
        session.flush()
        return response

    @staticmethod
    def _resolve_operation_aliases(operation: Operation, aliases: dict[str, str]) -> Operation:
        if not aliases:
            return operation
        reference_fields = {
            "pipeline_id", "parent_id", "source_task_id", "source_id",
            "target_task_id", "target_id", "task_id", "artifact_id",
            "artifact_root_id",
        }
        data = dict(operation.data)
        changed = False
        for field in reference_fields:
            raw = data.get(field)
            replacement = aliases.get(str(raw)) if raw is not None else None
            if replacement is not None:
                data[field] = replacement
                changed = True
        entity_id = operation.entity_id
        replacement = aliases.get(str(entity_id)) if entity_id is not None else None
        if replacement is not None:
            entity_id = UUID(replacement)
            changed = True
        return operation.model_copy(update={"data": data, "entity_id": entity_id}) if changed else operation

    def _entity(self, session: Session, model: type, project: Project, operation: Operation) -> Any:
        raw_id = operation.entity_id or operation.data.get("id")
        if not raw_id:
            raise DomainError(422, "missing_entity_id", f"{operation.type} requires entity_id")
        entity = session.get(model, str(raw_id))
        if entity is None or getattr(entity, "project_id", project.id) != project.id:
            raise DomainError(404, "entity_not_found", f"Entity for {operation.type} was not found")
        deleted_at = getattr(entity, "deleted_at", None)
        restore_types = {"pipeline.restore", "task.restore"}
        if deleted_at is not None and operation.type not in restore_types:
            raise DomainError(
                409,
                "entity_deleted",
                f"{operation.type} targets a deleted entity",
                {"entity_id": str(raw_id), "operation_type": operation.type},
            )
        if isinstance(entity, Pipeline):
            if (
                entity.archived_at is not None
                and operation.type != "pipeline.restore"
            ):
                raise DomainError(
                    409,
                    "entity_inactive",
                    f"{operation.type} targets an archived pipeline",
                    {"entity_id": entity.id, "operation_type": operation.type},
                )
        elif isinstance(entity, Task):
            pipeline = session.get(Pipeline, entity.pipeline_id)
            if (
                pipeline is None
                or pipeline.deleted_at is not None
                or pipeline.archived_at is not None
            ):
                raise DomainError(
                    409,
                    "entity_inactive",
                    f"{operation.type} targets a task in an inactive pipeline",
                    {"entity_id": entity.id, "operation_type": operation.type},
                )
        elif isinstance(entity, JournalEntry):
            task = session.get(Task, entity.task_id)
            pipeline = session.get(Pipeline, task.pipeline_id) if task is not None else None
            if (
                task is None
                or task.deleted_at is not None
                or pipeline is None
                or pipeline.deleted_at is not None
                or pipeline.archived_at is not None
            ):
                raise DomainError(
                    409,
                    "entity_inactive",
                    f"{operation.type} targets a journal on an inactive task",
                    {"entity_id": entity.id, "operation_type": operation.type},
                )
        elif isinstance(entity, TaskEdge):
            endpoints = session.scalars(
                select(Task).where(Task.id.in_([entity.source_id, entity.target_id]))
            ).all()
            endpoint_pipeline_ids = {item.pipeline_id for item in endpoints}
            active_endpoint_pipeline_ids = set(
                session.scalars(
                    select(Pipeline.id).where(
                        Pipeline.id.in_(endpoint_pipeline_ids),
                        Pipeline.project_id == project.id,
                        Pipeline.deleted_at.is_(None),
                        Pipeline.archived_at.is_(None),
                    )
                ).all()
            )
            if (
                len(endpoints) != 2
                or any(item.deleted_at is not None for item in endpoints)
                or active_endpoint_pipeline_ids != endpoint_pipeline_ids
            ):
                raise DomainError(
                    409,
                    "entity_inactive",
                    f"{operation.type} targets an edge with an inactive endpoint",
                    {"entity_id": entity.id, "operation_type": operation.type},
                )
        current = getattr(entity, "entity_version", None)
        if operation.expected_version is not None and current != operation.expected_version:
            raise DomainError(409, "entity_version_conflict", f"{operation.type} targets a stale entity", {"entity_id": str(raw_id), "expected": operation.expected_version, "current": current})
        return entity

    @staticmethod
    def _next_position(session: Session, model: type, project_id: str, **filters: Any) -> float:
        conditions = [model.project_id == project_id]
        conditions.extend(getattr(model, key) == value for key, value in filters.items())
        return float(session.scalar(select(func.max(model.order_index)).where(*conditions)) or 0) + 1

    def _apply_operation(self, session: Session, project: Project, operation: Operation, actor_type: str, actor_label: str, request_id: str, entity_aliases: dict[str, str]) -> dict[str, Any]:
        data = dict(operation.data)
        kind = operation.type
        before: Any = None
        changed = True
        entity_type = kind.split(".", 1)[0]

        if kind.startswith("project."):
            entity = project
            relink_warnings: list[dict[str, str]] = []
            if operation.expected_version is not None and project.entity_version != operation.expected_version:
                raise DomainError(409, "entity_version_conflict", "Project metadata is stale")
            before = _public_project(project)
            if kind == "project.update":
                for key in ("name", "description", "research_goal", "success_criteria"):
                    if key in data:
                        value = str(data[key]).strip()
                        if key == "name" and not value:
                            raise DomainError(
                                422, "missing_project_name", "Project name is required"
                            )
                        setattr(project, key, value)
                if "color" in data:
                    color = str(data["color"]).strip().lower()
                    if re.fullmatch(r"#[0-9a-f]{6}", color) is None:
                        raise DomainError(
                            422,
                            "invalid_project_color",
                            "Project color must be a six-digit hexadecimal color",
                        )
                    project.color = color
            elif kind == "project.archive":
                project.archived_at = utcnow()
            elif kind == "project.trash":
                project.trashed_at = utcnow()
            elif kind == "project.restore":
                project.trashed_at = None; project.archived_at = None
            elif kind == "project.relink":
                if actor_type == "agent":
                    raise DomainError(403, "agent_authority", "Agents cannot relink project roots")
                new_root = _validated_directory(str(data.get("root_path", "")), self.settings.allowed_roots)
                _validate_monitor_storage_separation(new_root, self.settings)
                duplicate = session.scalar(select(Project).where(Project.root_path == str(new_root), Project.id != project.id))
                if duplicate:
                    raise DomainError(409, "project_already_enrolled", "That folder is already enrolled")
                project.root_path = str(new_root)
                root = session.scalar(select(ArtifactRoot).where(ArtifactRoot.project_id == project.id, ArtifactRoot.is_project_root.is_(True)))
                if root:
                    root.root_path = str(new_root); root.entity_version += 1
                relink_warnings = self._revalidate_local_artifacts(session, project)
            after = _public_project(project)
            if kind == "project.update":
                changed = before != after
            if relink_warnings:
                after["artifact_warnings"] = relink_warnings
        elif kind == "planning_profile.update":
            if actor_type == "agent":
                raise DomainError(
                    403, "agent_authority", "Agents cannot alter planning profiles"
                )
            entity = session.get(PlanningProfile, project.id)
            assert entity is not None
            if operation.expected_version is not None and entity.entity_version != operation.expected_version:
                raise DomainError(409, "entity_version_conflict", "Planning profile is stale")
            before = _public_planning_profile(entity)
            self._update_planning_profile(session, project, entity, data)
            changed = before != _public_planning_profile(entity)
            if changed:
                entity.entity_version += 1
            after = _public_planning_profile(entity)
        elif kind == "scan_policy.update":
            if actor_type == "agent":
                raise DomainError(403, "agent_authority", "Agents cannot alter scan policy")
            entity = session.get(ScanPolicy, project.id)
            assert entity is not None
            if operation.expected_version is not None and entity.entity_version != operation.expected_version:
                raise DomainError(409, "entity_version_conflict", "Scan policy is stale")
            before = _public_scan_policy(entity)
            for public, column in {
                "preferred_sources": "preferred_sources_json",
                "include_globs": "include_globs_json",
                "exclude_globs": "exclude_globs_json",
                "sensitive_patterns": "sensitive_patterns_json",
                "readable_source_root_ids": "readable_source_root_ids_json",
            }.items():
                if public in data:
                    values = self._validated_string_list(
                        public,
                        data[public],
                        deduplicate=True,
                        case_insensitive=(public == "sensitive_patterns"),
                    )
                    if public == "preferred_sources":
                        for value in values:
                            path = Path(value)
                            if path.is_absolute() or ".." in path.parts:
                                raise DomainError(422, "unsafe_preferred_source", "Preferred sources must be project-relative")
                    if public == "readable_source_root_ids":
                        normalized_roots: list[str] = []
                        for raw_id in values:
                            root_id = _uuid(raw_id)
                            root = session.get(ArtifactRoot, root_id)
                            if root is None or root.project_id != project.id or root.is_project_root:
                                raise DomainError(422, "invalid_readable_source_root", "Readable source roots must be approved additional roots")
                            if root_id not in normalized_roots:
                                normalized_roots.append(root_id)
                        values = normalized_roots
                    setattr(entity, column, canonical_json(values))
            if "max_text_file_size" in data:
                value = data["max_text_file_size"]
                if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 20 * 1024 * 1024:
                    raise DomainError(422, "invalid_scan_limit", "Maximum text size must be between 1 KiB and 20 MiB")
                entity.max_text_bytes = value
            if "git_history_limit" in data:
                value = data["git_history_limit"]
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000:
                    raise DomainError(422, "invalid_git_limit", "Git history limit must be between 0 and 10,000")
                entity.git_history_limit = value
            if "allow_git_metadata" in data:
                if not isinstance(data["allow_git_metadata"], bool):
                    raise DomainError(422, "invalid_scan_policy", "allow_git_metadata must be a boolean")
                entity.allow_git_metadata = data["allow_git_metadata"]
            if "max_files_per_scan" in data:
                value = data["max_files_per_scan"]
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5_000:
                    raise DomainError(422, "invalid_scan_limit", "Maximum files per scan must be between 1 and 5,000")
                entity.max_files_per_scan = value
            if "max_total_text_bytes" in data:
                value = data["max_total_text_bytes"]
                if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 100 * 1024 * 1024:
                    raise DomainError(422, "invalid_scan_limit", "Maximum total text bytes must be between 1 KiB and 100 MiB")
                entity.max_total_text_bytes = value
            readable = json.loads(entity.readable_source_root_ids_json or "[]")
            requested_legacy = data.get("allow_outside_sources")
            if requested_legacy is not None and bool(requested_legacy) != bool(readable):
                raise DomainError(422, "derived_scan_policy_field", "allow_outside_sources is derived from readable_source_root_ids")
            entity.allow_outside_sources = bool(readable)
            entity.follow_symlinks = False
            changed = before != _public_scan_policy(entity)
            if changed:
                entity.entity_version += 1
            after = _public_scan_policy(entity)
        elif kind == "pipeline.create":
            flow = str(data.get("flow_mode", "sequential"))
            if flow not in FLOW_MODES:
                raise DomainError(422, "invalid_flow_mode", "Invalid pipeline flow mode")
            entity = Pipeline(id=_uuid(data.get("id") or operation.entity_id), project_id=project.id, title=str(data.get("title") or "").strip(), description=str(data.get("description") or ""), flow_mode=flow, order_index=float(data.get("position", self._next_position(session, Pipeline, project.id))))
            if not entity.title:
                raise DomainError(422, "missing_title", "Pipeline title is required")
            session.add(entity); session.flush(); after = _public_pipeline(entity)
        elif kind in {"pipeline.update", "pipeline.archive", "pipeline.delete", "pipeline.restore"}:
            entity = self._entity(session, Pipeline, project, operation); before = _public_pipeline(entity)
            if kind == "pipeline.update":
                for key in ("title", "description"):
                    if key in data: setattr(entity, key, str(data[key]))
                if "flow_mode" in data:
                    if data["flow_mode"] not in FLOW_MODES: raise DomainError(422, "invalid_flow_mode", "Invalid flow mode")
                    entity.flow_mode = str(data["flow_mode"])
                if "position" in data: entity.order_index = float(data["position"])
            elif kind == "pipeline.archive":
                entity.archived_at = utcnow()
            elif kind == "pipeline.delete":
                if entity.deleted_at is not None:
                    raise DomainError(409, "already_deleted", "Pipeline is already deleted")
                subtree = session.scalars(select(Task).where(Task.pipeline_id == entity.id, Task.deleted_at.is_(None))).all()
                if subtree and not data.get("cascade"):
                    raise DomainError(409, "pipeline_not_empty", "Set cascade=true to delete a non-empty pipeline")
                entity.deleted_at = utcnow()
                entity.deletion_batch_id = request_id
                self._delete_tasks_and_edges(session, project, subtree, request_id)
            else:
                was_deleted = entity.deleted_at is not None
                deletion_batch_id = entity.deletion_batch_id
                entity.deleted_at = None
                entity.archived_at = None
                entity.deletion_batch_id = None
                if was_deleted and deletion_batch_id:
                    subtree = session.scalars(select(Task).where(
                        Task.pipeline_id == entity.id,
                        Task.deletion_batch_id == deletion_batch_id,
                    )).all()
                    self._restore_tasks_and_edges(session, project, subtree, deletion_batch_id)
            entity.entity_version += 1; after = _public_pipeline(entity)
        elif kind == "task.create":
            pipeline_id = _uuid(data.get("pipeline_id"))
            pipeline = session.get(Pipeline, pipeline_id)
            if (
                pipeline is None
                or pipeline.project_id != project.id
                or pipeline.deleted_at is not None
                or pipeline.archived_at is not None
            ):
                raise DomainError(422, "invalid_pipeline", "Task pipeline is unavailable")
            parent_id = str(data["parent_id"]) if data.get("parent_id") else None
            if parent_id:
                parent = session.get(Task, parent_id)
                if parent is None or parent.project_id != project.id or parent.deleted_at is not None or parent.pipeline_id != pipeline_id:
                    raise DomainError(422, "invalid_parent", "Parent must be active in the same pipeline")
            entity = Task(id=_uuid(data.get("id") or operation.entity_id), project_id=project.id, pipeline_id=pipeline_id, parent_id=parent_id, user_key=data.get("user_key") or None, kind=str(data.get("kind") or "task"), title=str(data.get("title") or "").strip(), description=str(data.get("description") or ""), status=str(data.get("status") or "planned"), outcome=str(data.get("outcome") or "not_applicable"), priority=str(data.get("priority") or "recommended"), labels_json=canonical_json(data.get("labels") or []), target_date=data.get("target_date") or None, order_index=float(data.get("position", self._next_position(session, Task, project.id, pipeline_id=pipeline_id, parent_id=parent_id))), completion_criteria=str(data.get("completion_criteria") or ""), blocker_reason=str(data.get("blocker_reason") or ""), completion_summary=str(data.get("completion_summary") or ""), completion_actor="", completion_source="", completion_override_reason=str(data.get("completion_override_reason") or ""), completion_provenance="", child_flow_mode=str(data.get("child_flow_mode") or "freeform"))
            self._validate_task_fields(entity)
            if entity.status == "done":
                entity.completed_at = utcnow()
                entity.completion_actor = str(data.get("completion_actor") or actor_label)
                entity.completion_source = str(data.get("completion_source") or ("accepted_agent_proposal" if actor_type == "agent" else "manual_confirmation"))
                entity.completion_provenance = "agent" if actor_type == "agent" else "manual"
                self._guard_parent_completion(session, entity)
            session.add(entity); session.flush(); after = _public_task(entity)
        elif kind in {"task.update", "task.move", "task.delete", "task.restore"}:
            entity = self._entity(session, Task, project, operation); before = _public_task(entity)
            if kind in {"task.update", "task.move"}:
                self._update_task(session, project, entity, data, actor_type, actor_label)
            elif kind == "task.delete":
                if entity.deleted_at is not None:
                    raise DomainError(409, "already_deleted", "Task is already deleted")
                self._soft_delete_task_tree(session, project, entity, request_id)
            else:
                if entity.deleted_at is None:
                    raise DomainError(409, "not_deleted", "Task is not deleted")
                self._restore_task_tree(session, project, entity)
            if kind in {"task.update", "task.move"}:
                entity.entity_version += 1
            after = _public_task(entity)
        elif kind == "edge.create":
            source_id = _uuid(data.get("source_task_id") or data.get("source_id")); target_id = _uuid(data.get("target_task_id") or data.get("target_id"))
            if source_id == target_id: raise DomainError(422, "self_edge", "A task cannot depend on itself")
            self._require_tasks(session, project, [source_id, target_id])
            edge_type = str(data.get("edge_type", "dependency"))
            if edge_type not in EDGE_TYPES: raise DomainError(422, "invalid_edge_type", "Invalid edge type")
            # Related edges are semantically undirected. Store their endpoints in
            # one canonical order so API and agent clients cannot create both
            # A<->B and B<->A as distinct relationships.
            if edge_type == "related" and str(source_id) > str(target_id):
                source_id, target_id = target_id, source_id
            disabled = bool(data.get("disabled", False))
            tombstone = session.scalar(select(TaskEdge).where(
                TaskEdge.project_id == project.id,
                TaskEdge.source_id == source_id,
                TaskEdge.target_id == target_id,
                TaskEdge.edge_type == edge_type,
                TaskEdge.deleted_at.is_not(None),
            ))
            if tombstone is not None:
                entity = tombstone
                before = _public_edge(entity)
                entity.deleted_at = None
                entity.waived_reason = str(data.get("waiver_reason", ""))
                entity.enabled = not disabled
                entity.disabled_reason = "manual" if disabled else ""
                entity.disabled_batch_id = None
                entity.entity_version += 1
            else:
                entity = TaskEdge(id=_uuid(data.get("id") or operation.entity_id), project_id=project.id, source_id=source_id, target_id=target_id, edge_type=edge_type, waived_reason=str(data.get("waiver_reason", "")), enabled=not disabled, disabled_reason="manual" if disabled else "")
                session.add(entity)
            session.flush(); after = _public_edge(entity)
        elif kind in {"edge.update", "edge.delete"}:
            entity = self._entity(session, TaskEdge, project, operation); before = _public_edge(entity)
            if kind == "edge.delete": entity.deleted_at = utcnow()
            else:
                if "waiver_reason" in data: entity.waived_reason = str(data["waiver_reason"])
                if "disabled" in data:
                    entity.enabled = not bool(data["disabled"])
                    entity.disabled_reason = "manual" if not entity.enabled else ""
                    entity.disabled_batch_id = None
                if "edge_type" in data:
                    if data["edge_type"] not in EDGE_TYPES: raise DomainError(422, "invalid_edge_type", "Invalid edge type")
                    new_edge_type = str(data["edge_type"])
                    new_source_id, new_target_id = entity.source_id, entity.target_id
                    if new_edge_type == "related" and str(new_source_id) > str(new_target_id):
                        new_source_id, new_target_id = new_target_id, new_source_id
                    duplicate = session.scalar(select(TaskEdge.id).where(
                        TaskEdge.project_id == project.id,
                        TaskEdge.source_id == new_source_id,
                        TaskEdge.target_id == new_target_id,
                        TaskEdge.edge_type == new_edge_type,
                        TaskEdge.id != entity.id,
                    ))
                    if duplicate is not None:
                        raise DomainError(409, "duplicate_edge", "This task relationship already exists")
                    entity.source_id = new_source_id
                    entity.target_id = new_target_id
                    entity.edge_type = new_edge_type
            entity.entity_version += 1; after = _public_edge(entity)
        elif kind == "journal.create":
            task_id = _uuid(data.get("task_id")); self._require_tasks(session, project, [task_id])
            entry_type = str(data.get("entry_type", "note"))
            if entry_type not in JOURNAL_TYPES: raise DomainError(422, "invalid_journal_type", "Invalid journal entry type")
            content = str(data.get("content") or "")
            entity = JournalEntry(
                id=_uuid(data.get("id") or operation.entity_id),
                project_id=project.id,
                task_id=task_id,
                entry_type=entry_type,
                content=content,
                origin_key=str(data.get("_origin_key") or "") or None,
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                occurred_at=self._parse_datetime(data.get("occurred_at")) or utcnow(),
            )
            if not entity.content.strip(): raise DomainError(422, "empty_journal", "Journal content cannot be empty")
            session.add(entity)
            try:
                session.flush()
            except IntegrityError as exc:
                message = str(getattr(exc, "orig", exc))
                if entity.origin_key and "journal_entries" in message:
                    raise DomainError(409, "journal_origin_duplicate", "A journal with this automation origin already exists") from exc
                raise
            after = self._public_journal(entity)
        elif kind in {"journal.update", "journal.delete"}:
            entity = self._entity(session, JournalEntry, project, operation); before = self._public_journal(entity)
            if kind == "journal.delete": entity.deleted_at = utcnow()
            else:
                if "content" in data: entity.content = str(data["content"])
                if "entry_type" in data: entity.entry_type = str(data["entry_type"])
                if entity.entry_type not in JOURNAL_TYPES or not entity.content.strip(): raise DomainError(422, "invalid_journal", "Journal type and content are required")
                if "occurred_at" in data: entity.occurred_at = self._parse_datetime(data["occurred_at"]) or entity.occurred_at
                entity.content_sha256 = hashlib.sha256(entity.content.encode("utf-8")).hexdigest()
            entity.entity_version += 1; after = self._public_journal(entity)
        elif kind == "artifact_root.create":
            if actor_type == "agent": raise DomainError(403, "agent_authority", "Agents cannot approve artifact roots")
            root = _validated_directory(str(data.get("canonical_path") or data.get("root_path") or ""), self.settings.allowed_roots)
            _validate_monitor_storage_separation(root, self.settings)
            entity = ArtifactRoot(id=_uuid(data.get("id") or operation.entity_id), project_id=project.id, alias=str(data.get("name", root.name)), root_path=str(root), is_project_root=False)
            session.add(entity); session.flush(); after = _public_artifact_root(entity)
        elif kind == "artifact_root.delete":
            if actor_type == "agent": raise DomainError(403, "agent_authority", "Agents cannot remove artifact roots")
            entity = self._entity(session, ArtifactRoot, project, operation); before = _public_artifact_root(entity)
            if entity.is_project_root: raise DomainError(409, "project_root_required", "Project artifact root cannot be deleted")
            policy = session.get(ScanPolicy, project.id)
            readable_root_ids = set(
                json.loads(policy.readable_source_root_ids_json or "[]")
                if policy is not None
                else []
            )
            if entity.id in readable_root_ids:
                raise DomainError(
                    409,
                    "artifact_root_readable",
                    "Remove this root from the scan policy before deleting it",
                )
            if session.scalar(
                select(func.count()).select_from(SourceReference).where(
                    SourceReference.project_id == project.id,
                    SourceReference.source_root_id == entity.id,
                )
            ):
                raise DomainError(
                    409, "artifact_root_cited", "A cited artifact root cannot be removed"
                )
            if session.scalar(select(func.count()).select_from(Artifact).where(Artifact.root_id == entity.id)):
                raise DomainError(
                    409, "artifact_root_history_retained",
                    "This root has artifact history and cannot be removed in v1",
                )
            session.delete(entity); after = None
        elif kind == "artifact.create":
            locator_type = str(data.get("kind") or "local"); locator = str(data.get("locator") or ""); root_id = str(data.get("artifact_root_id") or "") or None
            requested_id = _uuid(data.get("id") or operation.entity_id)
            self._validate_artifact_locator(session, project, locator_type, locator, root_id)
            statement = select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.locator == locator,
                Artifact.deleted_at.is_not(None),
            )
            statement = statement.where(Artifact.root_id == root_id) if root_id else statement.where(Artifact.root_id.is_(None))
            tombstone = session.scalar(statement)
            if tombstone is not None:
                raise DomainError(
                    409,
                    "entity_deleted",
                    "Artifact locator belongs to a deleted artifact",
                    {"entity_id": tombstone.id, "field": "locator"},
                )
            entity = Artifact(id=requested_id, project_id=project.id, root_id=root_id, locator_type=locator_type, locator=locator, provider=str(data.get("provider") or ("local" if locator_type == "local" else "external")), label=str(data.get("label") or "") or Path(locator).name, notes=str(data.get("notes") or ""), validation_warning="")
            session.add(entity)
            session.flush(); after = _public_artifact(session, entity)
        elif kind in {"artifact.update", "artifact.delete"}:
            entity = self._entity(session, Artifact, project, operation); before = _public_artifact(session, entity)
            if kind == "artifact.delete": entity.deleted_at = utcnow()
            else:
                locator_type = str(data.get("kind", entity.locator_type)); locator = str(data.get("locator", entity.locator)); root_id = str(data.get("artifact_root_id", entity.root_id) or "") or None
                self._validate_artifact_locator(session, project, locator_type, locator, root_id, exclude_id=entity.id)
                entity.locator_type = locator_type; entity.locator = locator; entity.root_id = root_id
                entity.validation_warning = ""
                for key in ("provider", "label", "notes"):
                    if key in data: setattr(entity, key, str(data[key]))
            entity.entity_version += 1; after = _public_artifact(session, entity)
        elif kind == "task_artifact.link":
            task_id = _uuid(data.get("task_id")); artifact_id = _uuid(data.get("artifact_id")); self._require_tasks(session, project, [task_id])
            artifact = session.get(Artifact, artifact_id)
            if artifact is None or artifact.project_id != project.id or artifact.deleted_at is not None: raise DomainError(422, "invalid_artifact", "Artifact is unavailable")
            role = str(data.get("role", "reference"))
            if role not in ARTIFACT_ROLES: raise DomainError(422, "invalid_artifact_role", "Invalid artifact role")
            entity = TaskArtifact(id=_uuid(data.get("id") or operation.entity_id), project_id=project.id, task_id=task_id, artifact_id=artifact_id, role=role, notes=str(data.get("notes") or ""))
            session.add(entity); session.flush(); after = model_dict(entity)
        elif kind == "task_artifact.unlink":
            entity = self._entity(session, TaskArtifact, project, operation); before = model_dict(entity); session.delete(entity); after = None
        else:
            raise DomainError(422, "unknown_operation", f"Unsupported operation: {kind}")

        session.flush()
        entity_id = str(getattr(entity, "id", project.id))
        if changed:
            self._audit(
                session, project, actor_type, actor_label, kind, entity_type,
                entity_id, before, after, request_id,
                result_revision=project.semantic_revision + 1,
            )
        return {
            "operation_id": str(operation.id),
            "type": kind,
            "entity_id": entity_id,
            "value": after,
            "changed": changed,
        }

    def _update_task(self, session: Session, project: Project, task: Task, data: dict[str, Any], actor_type: str, actor_label: str) -> None:
        previous_status = task.status
        new_pipeline_id = str(data.get("pipeline_id", task.pipeline_id))
        new_parent_id = str(data["parent_id"]) if data.get("parent_id") else None
        if "parent_id" not in data: new_parent_id = task.parent_id
        pipeline = session.get(Pipeline, new_pipeline_id)
        if (
            pipeline is None
            or pipeline.project_id != project.id
            or pipeline.deleted_at is not None
            or pipeline.archived_at is not None
        ):
            raise DomainError(422, "invalid_pipeline", "Target pipeline is unavailable")
        if new_parent_id:
            parent = session.get(Task, new_parent_id)
            if parent is None or parent.project_id != project.id or parent.deleted_at is not None: raise DomainError(422, "invalid_parent", "Target parent is unavailable")
            parent_pipeline = session.get(Pipeline, parent.pipeline_id)
            if (
                parent_pipeline is None
                or parent_pipeline.deleted_at is not None
                or parent_pipeline.archived_at is not None
            ):
                raise DomainError(409, "entity_inactive", "Target parent is in an inactive pipeline")
            all_tasks = session.scalars(select(Task).where(Task.project_id == project.id)).all()
            if parent.id == task.id or parent.id in descendants(all_tasks).get(task.id, set()): raise DomainError(422, "hierarchy_cycle", "Cannot move a task under its descendant")
            new_pipeline_id = parent.pipeline_id
        task.parent_id = new_parent_id
        if new_pipeline_id != task.pipeline_id: self._move_subtree_pipeline(session, task, new_pipeline_id)
        for key in ("user_key", "kind", "title", "description", "status", "priority", "target_date", "completion_criteria", "blocker_reason", "completion_summary", "completion_override_reason", "child_flow_mode"):
            if key in data: setattr(task, key, data[key] if data[key] is not None else "")
        if "outcome" in data:
            task.outcome = str(data["outcome"] or "not_applicable")
        if "labels" in data: task.labels_json = canonical_json(data["labels"])
        if "position" in data: task.order_index = float(data["position"])
        self._validate_task_fields(task)
        if task.status == "done" and previous_status != "done":
            task.completed_at = utcnow()
            task.completion_actor = str(data.get("completion_actor") or actor_label)
            task.completion_source = str(data.get("completion_source") or ("accepted_agent_proposal" if actor_type == "agent" else "manual_confirmation"))
            task.completion_provenance = "agent" if actor_type == "agent" else "manual"
            self._guard_parent_completion(session, task)
        elif task.status == "done":
            # An ordinary edit to an already-complete task is not a new
            # confirmation event. Preserve the original provenance unless the
            # caller explicitly edits a completion-record field.
            if "completion_actor" in data:
                task.completion_actor = str(data.get("completion_actor") or "")
            if "completion_source" in data:
                task.completion_source = str(data.get("completion_source") or "")
            self._guard_parent_completion(session, task)
        elif previous_status == "done" or task.completed_at is not None:
            # Reopening invalidates the canonical completion record. The audit
            # event still retains the former values, while a later completion
            # receives fresh actor/source/provenance metadata.
            task.completed_at = None
            task.completion_actor = ""
            task.completion_source = ""
            task.completion_provenance = ""
            task.completion_summary = ""
            task.completion_override_reason = ""

    @staticmethod
    def _move_subtree_pipeline(session: Session, root: Task, pipeline_id: str) -> None:
        tasks = session.scalars(select(Task).where(Task.project_id == root.project_id)).all(); ids = descendants(tasks).get(root.id, set()); root.pipeline_id = pipeline_id
        for task in tasks:
            if task.id in ids: task.pipeline_id = pipeline_id; task.entity_version += 1

    @staticmethod
    def _delete_tasks_and_edges(session: Session, project: Project, tasks: list[Task], deletion_batch_id: str) -> None:
        ids = {task.id for task in tasks}; now = utcnow()
        for task in tasks:
            if task.deleted_at is not None:
                continue
            task.deleted_at = now; task.deletion_batch_id = deletion_batch_id; task.entity_version += 1
        for edge in session.scalars(select(TaskEdge).where(TaskEdge.project_id == project.id, TaskEdge.deleted_at.is_(None))):
            if (edge.source_id in ids or edge.target_id in ids) and edge.enabled:
                edge.enabled = False; edge.disabled_reason = "subtree_deleted"; edge.disabled_batch_id = deletion_batch_id; edge.entity_version += 1

    def _soft_delete_task_tree(self, session: Session, project: Project, root: Task, deletion_batch_id: str) -> None:
        tasks = session.scalars(select(Task).where(Task.project_id == project.id)).all(); ids = {root.id, *descendants(tasks).get(root.id, set())}
        self._delete_tasks_and_edges(session, project, [task for task in tasks if task.id in ids and task.deleted_at is None], deletion_batch_id)

    def _restore_task_tree(self, session: Session, project: Project, root: Task) -> None:
        deletion_batch_id = root.deletion_batch_id
        if not deletion_batch_id:
            raise DomainError(409, "restore_provenance_missing", "Task deletion provenance is unavailable")
        tasks = session.scalars(select(Task).where(Task.project_id == project.id)).all(); ids = {root.id, *descendants(tasks).get(root.id, set())}
        self._restore_tasks_and_edges(
            session, project,
            [task for task in tasks if task.id in ids and task.deletion_batch_id == deletion_batch_id],
            deletion_batch_id,
        )

    @staticmethod
    def _restore_tasks_and_edges(session: Session, project: Project, restored: list[Task], deletion_batch_id: str) -> None:
        if not restored:
            return
        restored_ids = {task.id for task in restored}
        for task in restored:
            task.deleted_at = None
            task.deletion_batch_id = None
            task.entity_version += 1
        session.flush()
        tasks = session.scalars(select(Task).where(Task.project_id == project.id)).all()
        pipelines = session.scalars(select(Pipeline).where(Pipeline.project_id == project.id)).all()
        edges = session.scalars(select(TaskEdge).where(TaskEdge.project_id == project.id, TaskEdge.deleted_at.is_(None))).all()
        active_ids = {task.id for task in tasks if task.deleted_at is None}
        for edge in edges:
            if edge.enabled or edge.disabled_reason != "subtree_deleted" or edge.disabled_batch_id != deletion_batch_id:
                continue
            if edge.source_id not in restored_ids and edge.target_id not in restored_ids:
                continue
            if edge.source_id not in active_ids or edge.target_id not in active_ids:
                edge.disabled_reason = "missing_endpoint"
                edge.disabled_batch_id = None
                edge.entity_version += 1
                continue
            edge.enabled = True
            edge.disabled_reason = ""
            edge.disabled_batch_id = None
            try:
                validate_dag(tasks, pipelines, edges)
            except GraphCycleError:
                edge.enabled = False
                edge.disabled_reason = "restore_conflict"
            edge.entity_version += 1

    @staticmethod
    def _validate_task_fields(task: Task) -> None:
        if not task.title.strip(): raise DomainError(422, "missing_title", "Task title is required")
        if task.status not in TASK_STATUSES: raise DomainError(422, "invalid_status", "Invalid task status")
        if task.priority not in TASK_PRIORITIES: raise DomainError(422, "invalid_priority", "Invalid task priority")
        if task.outcome not in TASK_OUTCOMES: raise DomainError(422, "invalid_outcome", "Invalid research outcome")
        if task.kind not in TASK_KINDS: raise DomainError(422, "invalid_task_kind", "Invalid task kind")
        if task.child_flow_mode not in FLOW_MODES: raise DomainError(422, "invalid_flow_mode", "Invalid child flow mode")
        if task.status == "blocked" and not task.blocker_reason.strip(): raise DomainError(422, "blocker_reason_required", "Blocked tasks require a blocker reason")
        if task.status == "done" and not task.completion_summary.strip(): raise DomainError(422, "completion_summary_required", "Done tasks require a completion summary")

    @staticmethod
    def _guard_parent_completion(session: Session, task: Task) -> None:
        all_tasks = session.scalars(select(Task).where(Task.project_id == task.project_id)).all()
        descendant_ids = descendants(all_tasks).get(task.id, set())
        incomplete = [
            item.id for item in all_tasks
            if item.id in descendant_ids and item.deleted_at is None and item.status not in {"done", "dropped"}
        ]
        if incomplete and not task.completion_override_reason.strip():
            raise DomainError(
                409,
                "incomplete_descendants",
                "A parent cannot be completed while descendants remain incomplete",
                {"override_field": "completion_override_reason", "task_ids": sorted(incomplete)},
            )

    @staticmethod
    def _require_tasks(session: Session, project: Project, task_ids: list[str]) -> None:
        found = session.scalars(
            select(Task).where(Task.id.in_(task_ids), Task.project_id == project.id)
        ).all()
        if {task.id for task in found} != set(task_ids):
            raise DomainError(422, "invalid_task", "One or more task references are unavailable")
        if any(task.deleted_at is not None for task in found):
            raise DomainError(409, "entity_deleted", "One or more task references are deleted")
        pipeline_ids = {task.pipeline_id for task in found}
        active_pipeline_ids = set(
            session.scalars(
                select(Pipeline.id).where(
                    Pipeline.id.in_(pipeline_ids),
                    Pipeline.project_id == project.id,
                    Pipeline.deleted_at.is_(None),
                    Pipeline.archived_at.is_(None),
                )
            ).all()
        )
        if active_pipeline_ids != pipeline_ids:
            raise DomainError(409, "entity_inactive", "One or more task references are in an inactive pipeline")

    def _validate_project(self, session: Session, project_id: str) -> None:
        pipelines = session.scalars(select(Pipeline).where(Pipeline.project_id == project_id)).all(); tasks = session.scalars(select(Task).where(Task.project_id == project_id)).all(); edges = session.scalars(select(TaskEdge).where(TaskEdge.project_id == project_id)).all(); active = {task.id: task for task in tasks if task.deleted_at is None}
        for task in active.values():
            if task.parent_id:
                parent = active.get(task.parent_id)
                if parent is None or parent.pipeline_id != task.pipeline_id: raise DomainError(422, "invalid_hierarchy", "Parent and child must be active in the same pipeline")
            self._validate_task_fields(task)
            if task.status == "done":
                self._guard_parent_completion(session, task)
        try: validate_dag(tasks, pipelines, edges)
        except GraphCycleError as exc: raise DomainError(422, "dependency_cycle", str(exc), {"path": exc.path}) from exc

    def _validate_artifact_locator(self, session: Session, project: Project, kind: str, locator: str, root_id: str | None, *, exclude_id: str | None = None) -> None:
        if kind == "url":
            try:
                parsed = parse_http_url(locator)
            except ValueError as exc:
                raise DomainError(422, "unsafe_url", "External artifacts require HTTP or HTTPS") from exc
            if parsed.username is not None or parsed.password is not None:
                raise DomainError(422, "artifact_url_credentials", "Artifact URLs cannot contain credentials")
            for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
                folded = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
                tokens = {token for token in folded.split("_") if token}
                if (
                    any(
                        value in folded
                        for value in (
                            "access_token", "api_key", "secret", "password",
                            "credential",
                        )
                    )
                    or tokens
                    & {"auth", "authorization", "bearer", "key", "sig", "signature", "token"}
                ):
                    raise DomainError(422, "artifact_url_secret", "Artifact URL contains a suspicious credential parameter")
            if root_id: raise DomainError(422, "url_with_root", "URL artifacts cannot have an artifact root")
            self._ensure_unique_artifact_locator(session, project, kind, locator, None, exclude_id)
            return
        if kind != "local": raise DomainError(422, "invalid_artifact_kind", "Artifact kind must be local or url")
        if not root_id: raise DomainError(422, "artifact_root_required", "Local artifacts require an approved root")
        root = session.get(ArtifactRoot, root_id)
        if root is None or root.project_id != project.id: raise DomainError(422, "invalid_artifact_root", "Artifact root is unavailable")
        relative = Path(locator)
        if not locator or relative.is_absolute() or ".." in relative.parts: raise DomainError(422, "unsafe_artifact_path", "Local artifact locator must be relative")
        _artifact_path(session, Artifact(project_id=project.id, root_id=root_id, locator_type="local", locator=locator))

        self._ensure_unique_artifact_locator(session, project, kind, locator, root_id, exclude_id)

    @staticmethod
    def _ensure_unique_artifact_locator(session: Session, project: Project, kind: str, locator: str, root_id: str | None, exclude_id: str | None) -> None:
        statement = select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.locator_type == kind,
            Artifact.locator == locator,
            Artifact.deleted_at.is_(None),
        )
        statement = statement.where(Artifact.root_id == root_id) if root_id else statement.where(Artifact.root_id.is_(None))
        if exclude_id:
            statement = statement.where(Artifact.id != exclude_id)
        if session.scalar(statement) is not None:
            raise DomainError(409, "artifact_already_linked", "This artifact locator is already linked in the project")

    @staticmethod
    def _revalidate_local_artifacts(session: Session, project: Project) -> list[dict[str, str]]:
        warnings: list[dict[str, str]] = []
        artifacts = session.scalars(select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.locator_type == "local",
            Artifact.deleted_at.is_(None),
        )).all()
        for artifact in artifacts:
            warning = ""
            code = ""
            message = ""
            try:
                _artifact_path(session, artifact, must_exist=True)
            except DomainError as exc:
                code = exc.code
                message = exc.message
                warning = f"{code}: {message}"
            if artifact.validation_warning != warning:
                artifact.validation_warning = warning
                artifact.entity_version += 1
            if warning:
                warnings.append({
                    "artifact_id": artifact.id,
                    "locator": artifact.locator,
                    "code": code,
                    "message": message,
                })
        return warnings

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value in (None, ""): return None
        if isinstance(value, datetime): return value
        try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc: raise DomainError(422, "invalid_datetime", f"Invalid timestamp: {value}") from exc

    def _update_planning_profile(
        self,
        session: Session,
        project: Project,
        entity: PlanningProfile,
        data: dict[str, Any],
    ) -> None:
        enums = {
            "task_granularity": {"coarse", "balanced", "detailed"},
            "planning_horizon": {"immediate", "current_milestone", "whole_project"},
            "inference_policy": {"sources_only", "cautious_gaps", "broad_roadmap"},
        }
        for field, allowed in enums.items():
            if field in data:
                value = str(data[field])
                if value not in allowed:
                    raise DomainError(422, "invalid_planning_profile", f"Invalid {field}")
                setattr(entity, field, value)
        for field, lower, upper in (
            ("max_nesting_depth", 1, 6),
            ("max_new_tasks_per_proposal", 1, 100),
        ):
            if field in data:
                value = data[field]
                if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                    raise DomainError(
                        422, "invalid_planning_profile",
                        f"{field} must be between {lower} and {upper}",
                    )
                setattr(entity, field, value)
        if "preferred_pipeline_names" in data:
            values = self._validated_string_list(
                "preferred_pipeline_names",
                data["preferred_pipeline_names"],
                deduplicate=True,
                case_insensitive=True,
            )
            if len(values) > 20:
                raise DomainError(
                    422, "invalid_planning_profile",
                    "Preferred pipeline names must contain at most 20 values",
                )
            entity.preferred_pipeline_names_json = canonical_json(values)
        for field, maximum in (
            ("terminology_notes", 4096),
            ("additional_instructions", 8192),
        ):
            if field in data:
                value = str(data[field])
                if len(value.encode("utf-8")) > maximum:
                    raise DomainError(422, "invalid_planning_profile", f"{field} exceeds its UTF-8 byte limit")
                setattr(entity, field, value)
        for field, column, model in (
            ("protected_pipeline_ids", "protected_pipeline_ids_json", Pipeline),
            ("protected_task_ids", "protected_task_ids_json", Task),
        ):
            if field not in data:
                continue
            values = self._validated_string_list(field, data[field])
            normalized = [_uuid(value) for value in values]
            if len(normalized) > 500 or len(normalized) != len(set(normalized)):
                raise DomainError(422, "invalid_planning_profile", f"{field} must contain at most 500 unique IDs")
            for value in normalized:
                target = session.get(model, value)
                if target is None or target.project_id != project.id:
                    raise DomainError(422, "invalid_protected_entity", f"{field} contains an entity outside this project")
            setattr(entity, column, canonical_json(normalized))

    @staticmethod
    def _validated_string_list(
        field: str,
        value: Any,
        *,
        deduplicate: bool = False,
        case_insensitive: bool = False,
    ) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise DomainError(422, "invalid_scan_policy", f"{field} must be a list of strings")
        result = [item.strip() for item in value]
        if any(not item or len(item) > 500 or "\x00" in item for item in result):
            raise DomainError(422, "invalid_scan_policy", f"{field} contains an invalid pattern")
        if deduplicate:
            unique: list[str] = []
            seen: set[str] = set()
            for item in result:
                identity = item.casefold() if case_insensitive else item
                if identity in seen:
                    continue
                seen.add(identity)
                unique.append(item)
            result = unique
        if len(result) > 1_000:
            raise DomainError(
                422, "invalid_scan_policy", f"{field} contains too many values"
            )
        return result

    def _apply_layout_operation(self, session: Session, project: Project, operation: Operation) -> dict[str, Any]:
        if operation.type == "viewport.upsert":
            data = operation.data
            scope_id = str(data.get("parent_id") or "root")
            if scope_id != "root":
                self._require_tasks(session, project, [_uuid(scope_id)])
            zoom = float(data.get("zoom", 1))
            if not 0.05 <= zoom <= 10:
                raise DomainError(422, "invalid_viewport_zoom", "Viewport zoom must be between 0.05 and 10")
            viewport = session.scalar(select(GraphViewport).where(
                GraphViewport.project_id == project.id,
                GraphViewport.scope_id == scope_id,
            ))
            if viewport is None:
                viewport = GraphViewport(
                    id=str(uuid4()), project_id=project.id,
                    scope_id=scope_id, x=float(data.get("x", 0)),
                    y=float(data.get("y", 0)), zoom=zoom,
                )
                session.add(viewport)
            else:
                if operation.expected_version is not None and viewport.entity_version != operation.expected_version:
                    raise DomainError(409, "entity_version_conflict", "Graph viewport is stale")
                viewport.x = float(data.get("x", viewport.x)); viewport.y = float(data.get("y", viewport.y))
                viewport.zoom = float(data.get("zoom", viewport.zoom)); viewport.entity_version += 1
            session.flush(); value = self._public_viewport(viewport)
            return {"operation_id": str(operation.id), "type": operation.type, "entity_id": viewport.id, "value": value}
        if operation.type == "layout.delete":
            layout = self._entity(session, TaskLayout, project, operation); layout_id = layout.id; session.delete(layout)
            return {"operation_id": str(operation.id), "type": operation.type, "entity_id": layout_id, "value": None}
        data = operation.data; task_id = _uuid(data.get("task_id")); self._require_tasks(session, project, [task_id]); scope_id = str(data.get("parent_id") or "root")
        layout = session.scalar(select(TaskLayout).where(TaskLayout.project_id == project.id, TaskLayout.task_id == task_id, TaskLayout.scope_id == scope_id))
        if layout is None:
            layout = TaskLayout(id=str(uuid4()), project_id=project.id, task_id=task_id, scope_id=scope_id, x=float(data.get("x", 0)), y=float(data.get("y", 0))); session.add(layout)
        else:
            if operation.expected_version is not None and layout.entity_version != operation.expected_version: raise DomainError(409, "entity_version_conflict", "Layout position is stale")
            layout.x = float(data.get("x", layout.x)); layout.y = float(data.get("y", layout.y)); layout.entity_version += 1
        session.flush(); value = self._public_layout(layout)
        return {"operation_id": str(operation.id), "type": operation.type, "entity_id": layout.id, "value": value}
