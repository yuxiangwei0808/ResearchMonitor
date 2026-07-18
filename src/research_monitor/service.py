from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import API_VERSION, SCHEMA_VERSION
from .config import Settings
from .graph import GraphCycleError, compute_readiness, descendants, validate_dag
from .models import (
    Artifact,
    GraphViewport,
    ArtifactRoot,
    AgentIntent,
    AuditEvent,
    IdempotencyRecord,
    JournalEntry,
    OutboxEvent,
    Pipeline,
    PlanningProfile,
    Project,
    Proposal,
    ProposalOperation,
    ScanPolicy,
    SourceReference,
    Task,
    TaskArtifact,
    TaskEdge,
    TaskLayout,
    utcnow,
)
from .schemas import LayoutMutationEnvelope, MutationEnvelope, Operation, ProjectCreate, ProposalEnvelope
from .serializers import canonical_json, jsonable, model_dict, project_dict


TASK_STATUSES = {"planned", "in_progress", "blocked", "review", "done", "dropped"}
TASK_PRIORITIES = {"required", "recommended", "optional", "conditional"}
TASK_OUTCOMES = {"successful", "negative", "inconclusive", "failed", "not_applicable"}
TASK_KINDS = {"task", "milestone", "gate"}
FLOW_MODES = {"sequential", "freeform"}
JOURNAL_TYPES = {"progress", "decision", "blocker", "note", "completion"}
EDGE_TYPES = {"dependency", "related"}
ARTIFACT_ROLES = {
    "input", "code", "document", "log", "result", "checkpoint", "figure",
    "dataset", "evidence", "reference", "external_run",
}
SEARCH_ENTITY_TYPES = {"task", "journal", "artifact"}
READINESS_STATES = {"ready", "waiting", "blocked", "inconsistent"}
SNAPSHOT_SECTIONS = {
    "project",
    "automation_state",
    "scan_policy",
    "planning_profile",
    "artifact_roots",
    "pipelines",
    "tasks",
    "edges",
    "journals",
    "artifacts",
    "task_artifacts",
    "layouts",
    "viewports",
    "progress",
}

DEFAULT_EXCLUDES = [
    "**/.git/**", "**/.env*", "**/.ssh/**", "**/.aws/**", "**/.gnupg/**",
    "**/*credential*", "**/*token*", "**/*cert*", "**/*.key",
    "**/*.pem", "**/*.p12", "**/*.pfx", "**/*.crt", "**/*.cer", "**/*.der",
    "**/node_modules/**", "**/.venv/**", "**/venv/**", "**/data/**",
    "**/checkpoints/**", "**/wandb/**", "**/mlruns/**",
]
DEFAULT_SENSITIVE = [
    ".env", ".ssh", ".aws", ".gnupg", "credential", "credentials", "secret", "token", "cert", "certificate", ".key", ".pem", ".p12", ".pfx", ".crt", ".cer", ".der",
]


class DomainError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)

    def as_detail(self) -> dict[str, Any]:
        value = {"code": self.code, "message": self.message}
        if self.details is not None:
            value["details"] = jsonable(self.details)
        return value


def _uuid(value: Any = None) -> str:
    if value in (None, ""):
        return str(uuid4())
    try:
        return str(UUID(str(value)))
    except ValueError as exc:
        raise DomainError(422, "invalid_uuid", f"Invalid UUID: {value}") from exc


def _inside(path: Path, roots: Iterable[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _validated_directory(raw_path: str, allowed_roots: Iterable[Path]) -> Path:
    path = Path(raw_path).expanduser()
    if not path.exists() or not path.is_dir():
        raise DomainError(422, "invalid_project_root", "Project root must be an existing directory")
    resolved = path.resolve(strict=True)
    if not _inside(resolved, allowed_roots):
        raise DomainError(403, "root_not_allowed", "Project root is outside configured allowed roots")
    return resolved


def _managed_monitor_paths(settings: Settings) -> tuple[tuple[str, Path], ...]:
    """Return canonical paths reserved for monitor state and recovery data."""

    candidates = (
        ("backup directory", settings.database_path.parent / "backups"),
        ("database", settings.database_path),
        ("configuration directory", settings.config_dir),
        ("runtime directory", settings.runtime_dir),
        ("data directory", settings.data_dir),
        ("configuration file", settings.config_path),
        ("runtime descriptor", settings.runtime_descriptor),
        ("host-local application lock", settings.lock_path),
        ("shared writer lock", settings.shared_lock_path),
        ("CLI token", settings.runtime_dir / "cli-token"),
    )
    result: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for label, candidate in candidates:
        canonical = candidate.expanduser().resolve(strict=False)
        if canonical not in seen:
            seen.add(canonical)
            result.append((label, canonical))
    return tuple(result)


def _validate_monitor_storage_separation(root: Path, settings: Settings) -> None:
    """Reject research roots that contain, or live inside, monitor-owned storage."""

    canonical_root = root.expanduser().resolve(strict=True)
    for label, managed_path in _managed_monitor_paths(settings):
        if _inside(canonical_root, [managed_path]) or _inside(managed_path, [canonical_root]):
            raise DomainError(
                422,
                "root_overlaps_monitor_storage",
                "Research and artifact roots cannot overlap Research Monitor storage",
                {
                    "root_path": str(canonical_root),
                    "managed_path": str(managed_path),
                    "managed_kind": label,
                },
            )


def _public_project(project: Project) -> dict[str, Any]:
    value = project_dict(project)
    value.update(
        archived=project.archived_at is not None,
        trashed=project.trashed_at is not None,
        unavailable=value["availability"] != "available",
        last_manual_update=jsonable(project.last_manual_update_at),
        # New clients use the same public version key as every other semantic
        # entity. Keep entity_version for compatibility with released v1
        # callers and exports.
        version=project.entity_version,
    )
    return value


def _public_pipeline(item: Pipeline) -> dict[str, Any]:
    value = model_dict(item, exclude={"deletion_batch_id"})
    value.update(position=item.order_index, archived=item.archived_at is not None, version=item.entity_version)
    return value


def _public_task(item: Task, readiness: dict[str, Any] | None = None) -> dict[str, Any]:
    value = model_dict(item, exclude={"deletion_batch_id"})
    for field in ("completion_actor", "completion_source", "completion_provenance"):
        if not value.get(field):
            value[field] = None
    value.update(position=item.order_index, version=item.entity_version)
    value.update(readiness or {"readiness": "ready", "unsatisfied_predecessor_ids": [], "predecessor_ids": []})
    return value


def _public_edge(item: TaskEdge) -> dict[str, Any]:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "source_task_id": item.source_id,
        "target_task_id": item.target_id,
        "edge_type": item.edge_type,
        "waived": bool(item.waived_reason),
        "waiver_reason": item.waived_reason or None,
        "disabled": not item.enabled,
        "disabled_reason": item.disabled_reason or None,
        "deleted_at": jsonable(item.deleted_at),
        "version": item.entity_version,
    }


def _public_scan_policy(item: ScanPolicy) -> dict[str, Any]:
    value = model_dict(item, exclude={"project_id"})
    value["max_text_file_size"] = value.pop("max_text_bytes")
    value["version"] = value.pop("entity_version")
    return value


def _public_planning_profile(item: PlanningProfile) -> dict[str, Any]:
    value = model_dict(item, exclude={"project_id", "created_at", "updated_at"})
    value["version"] = value.pop("entity_version")
    return value


def _public_artifact_root(item: ArtifactRoot) -> dict[str, Any]:
    return {
        "id": item.id,
        "project_id": item.project_id,
        "name": item.alias,
        "canonical_path": item.root_path,
        "is_project_root": item.is_project_root,
        "version": item.entity_version,
    }


def _artifact_path(session: Session, artifact: Artifact, *, must_exist: bool = False) -> Path:
    if artifact.locator_type != "local" or not artifact.root_id:
        raise DomainError(422, "not_local_artifact", "Artifact is not a local path")
    root = session.get(ArtifactRoot, artifact.root_id)
    if root is None or root.project_id != artifact.project_id:
        raise DomainError(404, "artifact_root_not_found", "Artifact root no longer exists")
    relative = Path(artifact.locator)
    if relative.is_absolute() or ".." in relative.parts:
        raise DomainError(403, "unsafe_artifact_path", "Artifact locator must be a safe relative path")
    try:
        stored_root = Path(root.root_path)
        root_path = stored_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise DomainError(404, "artifact_root_unavailable", "Approved artifact root is unavailable") from exc
    if root_path != stored_root:
        raise DomainError(403, "artifact_root_replaced", "Approved artifact root was replaced or redirected")
    candidate = root_path / relative
    if must_exist:
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise DomainError(404, "artifact_missing", "Artifact does not exist") from exc
        if not _inside(resolved, [root_path]):
            raise DomainError(403, "artifact_escape", "Artifact resolves outside its approved root")
        return resolved
    if candidate.exists():
        resolved = candidate.resolve(strict=True)
        if not _inside(resolved, [root_path]):
            raise DomainError(403, "artifact_escape", "Artifact resolves outside its approved root")
    return candidate


def _public_artifact(session: Session, item: Artifact, *, refresh: bool = False) -> dict[str, Any]:
    available: bool | None = False if item.validation_warning else None
    mime_type: str | None = None
    size_bytes: int | None = None
    if refresh and item.locator_type == "local":
        try:
            path = _artifact_path(session, item)
            available = path.exists()
            if available and path.is_file():
                mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                size_bytes = path.stat().st_size
        except DomainError:
            available = False
    return {
        "id": item.id,
        "project_id": item.project_id,
        "artifact_root_id": item.root_id,
        "locator": item.locator,
        "kind": item.locator_type,
        "provider": item.provider,
        "label": item.label,
        "notes": item.notes,
        "validation_warning": item.validation_warning or None,
        "available": available,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "updated_at": jsonable(item.updated_at),
        "deleted_at": jsonable(item.deleted_at),
        "version": item.entity_version,
    }


class ResearchMonitorService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _project(self, session: Session, project_id: str) -> Project:
        item = session.get(Project, str(project_id))
        if item is None:
            raise DomainError(404, "project_not_found", "Project not found")
        return item

    def create_project(self, session: Session, payload: ProjectCreate) -> dict[str, Any]:
        root = _validated_directory(payload.root_path, self.settings.allowed_roots)
        _validate_monitor_storage_separation(root, self.settings)
        if session.scalar(select(Project).where(Project.root_path == str(root))) is not None:
            raise DomainError(409, "project_already_enrolled", "That folder is already enrolled")
        project = Project(
            id=str(uuid4()), name=payload.name.strip(), root_path=str(root),
            description=payload.description, research_goal=payload.research_goal,
            success_criteria=payload.success_criteria, color=payload.color,
        )
        session.add(project)
        session.flush()
        session.add(
            ScanPolicy(
                project_id=project.id,
                exclude_globs_json=canonical_json(DEFAULT_EXCLUDES),
                sensitive_patterns_json=canonical_json(DEFAULT_SENSITIVE),
            )
        )
        session.add(PlanningProfile(project_id=project.id))
        session.add(
            ArtifactRoot(
                id=str(uuid4()), project_id=project.id, alias="Project root",
                root_path=str(root), is_project_root=True,
            )
        )
        session.flush()
        self._audit(session, project, "system", "", "project.enroll", "project", project.id, None, _public_project(project), "")
        return _public_project(project)

    def list_projects(self, session: Session, include_archived: bool = True, include_trashed: bool = False) -> list[dict[str, Any]]:
        statement = select(Project)
        if not include_archived:
            statement = statement.where(Project.archived_at.is_(None))
        if not include_trashed:
            statement = statement.where(Project.trashed_at.is_(None))
        projects = session.scalars(statement.order_by(Project.updated_at.desc(), Project.name)).all()
        result = []
        for project in projects:
            public = _public_project(project)
            public["progress"] = self._progress(session, project.id)
            result.append(public)
        return result

    def resolve_project(self, session: Session, raw_path: str) -> dict[str, Any]:
        try:
            path = Path(raw_path).expanduser().resolve(strict=True)
        except FileNotFoundError as exc:
            raise DomainError(422, "path_not_found", "Path does not exist") from exc
        matches: list[tuple[int, Project]] = []
        for project in session.scalars(select(Project).where(Project.trashed_at.is_(None))):
            root = Path(project.root_path)
            if _inside(path, [root]):
                matches.append((len(root.parts), project))
        if not matches:
            raise DomainError(404, "project_not_found", "No enrolled project contains this path")
        matches.sort(key=lambda value: value[0], reverse=True)
        deepest = matches[0][0]
        candidates = [project for length, project in matches if length == deepest]
        if len(candidates) != 1:
            raise DomainError(409, "ambiguous_project", "Path matches multiple equally specific projects")
        return _public_project(candidates[0])

    def snapshot(
        self,
        session: Session,
        project_id: str,
        sections: set[str] | None = None,
    ) -> dict[str, Any]:
        project = self._project(session, project_id)
        requested = SNAPSHOT_SECTIONS if sections is None else set(sections)
        unknown = sorted(requested - SNAPSHOT_SECTIONS)
        if unknown:
            raise DomainError(
                422,
                "invalid_snapshot_section",
                "Snapshot request contains unknown sections",
                {"sections": unknown, "allowed": sorted(SNAPSHOT_SECTIONS)},
            )

        # Task readiness and progress always use the complete semantic graph,
        # even when the response omits graph collections for a lighter view.
        needs_task_graph = bool(requested & {"tasks", "progress"})
        needs_pipelines = needs_task_graph or "pipelines" in requested
        needs_edges = needs_task_graph or "edges" in requested
        pipelines = (
            session.scalars(
                select(Pipeline)
                .where(Pipeline.project_id == project.id)
                .order_by(Pipeline.order_index)
            ).all()
            if needs_pipelines
            else []
        )
        tasks = (
            session.scalars(
                select(Task)
                .where(Task.project_id == project.id)
                .order_by(Task.order_index)
            ).all()
            if needs_task_graph
            else []
        )
        edges = (
            session.scalars(
                select(TaskEdge).where(TaskEdge.project_id == project.id)
            ).all()
            if needs_edges
            else []
        )
        active_pipelines = [
            pipeline for pipeline in pipelines
            if pipeline.deleted_at is None and pipeline.archived_at is None
        ]
        active_pipeline_ids = {pipeline.id for pipeline in active_pipelines}
        active_tasks = [
            task for task in tasks
            if task.deleted_at is None and task.pipeline_id in active_pipeline_ids
        ]
        readiness = compute_readiness(active_tasks, active_pipelines, edges)
        roots = (
            session.scalars(
                select(ArtifactRoot).where(ArtifactRoot.project_id == project.id)
            ).all()
            if "artifact_roots" in requested
            else []
        )
        journals = (
            session.scalars(
                select(JournalEntry)
                .where(JournalEntry.project_id == project.id)
                .order_by(JournalEntry.occurred_at.desc())
            ).all()
            if "journals" in requested
            else []
        )
        artifacts = (
            session.scalars(
                select(Artifact).where(Artifact.project_id == project.id)
            ).all()
            if "artifacts" in requested
            else []
        )
        links = (
            session.scalars(
                select(TaskArtifact).where(TaskArtifact.project_id == project.id)
            ).all()
            if "task_artifacts" in requested
            else []
        )
        layouts = (
            session.scalars(
                select(TaskLayout).where(TaskLayout.project_id == project.id)
            ).all()
            if "layouts" in requested
            else []
        )
        viewports = (
            session.scalars(
                select(GraphViewport).where(GraphViewport.project_id == project.id)
            ).all()
            if "viewports" in requested
            else []
        )
        policy = session.get(ScanPolicy, project.id)
        assert policy is not None
        profile = session.get(PlanningProfile, project.id)
        profile = profile or PlanningProfile(project_id=project.id)
        automation_state: dict[str, int] | None = None
        if "automation_state" in requested:
            now = utcnow()
            automation_state = {
                "active_intent_count": int(
                    session.scalar(
                        select(func.count())
                        .select_from(AgentIntent)
                        .where(
                            AgentIntent.project_id == project.id,
                            AgentIntent.expires_at > now,
                            AgentIntent.consumed_proposal_id.is_(None),
                            AgentIntent.issued_semantic_revision
                            == project.semantic_revision,
                            AgentIntent.planning_profile_version
                            == profile.entity_version,
                        )
                    )
                    or 0
                ),
                "open_draft_count": int(
                    session.scalar(
                        select(func.count())
                        .select_from(Proposal)
                        .where(
                            Proposal.project_id == project.id,
                            Proposal.status == "draft",
                            Proposal.base_semantic_revision
                            == project.semantic_revision,
                        )
                    )
                    or 0
                ),
            }
        descendant_map = descendants(tasks)
        task_values = []
        for item in tasks:
            value = _public_task(item, readiness.get(item.id))
            if item.status == "done" and item.completion_override_reason:
                incomplete = [
                    task.id for task in tasks
                    if task.id in descendant_map.get(item.id, set())
                    and task.deleted_at is None
                    and task.status not in {"done", "dropped"}
                ]
                if incomplete:
                    value["consistency_warning"] = (
                        f"Completion override recorded while {len(incomplete)} descendant task"
                        f"{'s remain' if len(incomplete) != 1 else ' remains'} incomplete."
                    )
                    value["incomplete_descendant_ids"] = sorted(incomplete)
            task_values.append(value)
        return {
            "project": _public_project(project),
            "automation_state": automation_state,
            "scan_policy": _public_scan_policy(policy),
            "planning_profile": (
                _public_planning_profile(profile)
                if "planning_profile" in requested
                else None
            ),
            "artifact_roots": [_public_artifact_root(item) for item in roots],
            "pipelines": [_public_pipeline(item) for item in pipelines] if "pipelines" in requested else [],
            "tasks": task_values if "tasks" in requested else [],
            "edges": [_public_edge(item) for item in edges] if "edges" in requested else [],
            "journals": [self._public_journal(item) for item in journals],
            "artifacts": [_public_artifact(session, item) for item in artifacts],
            "task_artifacts": [model_dict(item, exclude={"project_id", "created_at"}) for item in links],
            "layouts": [self._public_layout(item) for item in layouts],
            "viewports": [self._public_viewport(item) for item in viewports],
            "progress": (
                self._progress_from(active_tasks, readiness)
                if "progress" in requested
                else {
                    "leaf_total": 0,
                    "leaf_done": 0,
                    "ready": 0,
                    "waiting": 0,
                    "blocked": 0,
                    "review": 0,
                    "by_status": {},
                    "by_outcome": {},
                }
            ),
        }

    def search(
        self,
        session: Session,
        project_id: str,
        query: str,
        *,
        entity_types: set[str] | None = None,
        status: str | None = None,
        priority: str | None = None,
        readiness_state: str | None = None,
        label: str | None = None,
        artifact_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search indexed task, journal, and artifact text within one project."""

        self._project(session, project_id)
        requested_types = entity_types or SEARCH_ENTITY_TYPES
        invalid_types = requested_types - SEARCH_ENTITY_TYPES
        if invalid_types:
            raise DomainError(
                422,
                "invalid_search_entity_type",
                "Unknown search entity type",
                sorted(invalid_types),
            )
        if status is not None and status not in TASK_STATUSES:
            raise DomainError(422, "invalid_status", "Invalid task status")
        if priority is not None and priority not in TASK_PRIORITIES:
            raise DomainError(422, "invalid_priority", "Invalid task priority")
        if readiness_state is not None and readiness_state not in READINESS_STATES:
            raise DomainError(422, "invalid_readiness", "Invalid readiness filter")

        # Quoted prefix terms retain useful type-ahead behavior while preventing
        # user input from becoming arbitrary FTS5 query syntax.
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        if not tokens:
            raise DomainError(
                422,
                "invalid_search_query",
                "Search query must contain at least one letter or number",
            )
        match_query = " AND ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens
        )
        type_parameters = {
            f"entity_type_{index}": entity_type
            for index, entity_type in enumerate(sorted(requested_types))
        }
        type_clause = ", ".join(f":{name}" for name in type_parameters)
        candidate_limit = 10_000
        rows = session.execute(
            text(
                f"""
                SELECT
                  entity_type,
                  entity_id,
                  title,
                  snippet(research_search, 4, '', '', ' … ', 24) AS snippet,
                  bm25(research_search, 0.0, 0.0, 0.0, 8.0, 1.0) AS rank
                FROM research_search
                WHERE research_search MATCH :match_query
                  AND project_id = :project_id
                  AND entity_type IN ({type_clause})
                ORDER BY rank, entity_type, entity_id
                LIMIT :candidate_limit
                """
            ),
            {
                "match_query": match_query,
                "project_id": project_id,
                "candidate_limit": candidate_limit,
                **type_parameters,
            },
        ).mappings().all()

        task_ids = {
            str(row["entity_id"]) for row in rows if row["entity_type"] == "task"
        }
        journal_ids = {
            str(row["entity_id"]) for row in rows if row["entity_type"] == "journal"
        }
        artifact_ids = {
            str(row["entity_id"]) for row in rows if row["entity_type"] == "artifact"
        }
        tasks = {
            item.id: item
            for item in session.scalars(select(Task).where(Task.id.in_(task_ids)))
        } if task_ids else {}
        journals = {
            item.id: item
            for item in session.scalars(
                select(JournalEntry).where(JournalEntry.id.in_(journal_ids))
            )
        } if journal_ids else {}
        artifacts = {
            item.id: item
            for item in session.scalars(select(Artifact).where(Artifact.id.in_(artifact_ids)))
        } if artifact_ids else {}

        task_readiness: dict[str, dict[str, Any]] = {}
        pipeline_by_id: dict[str, Pipeline] = {}
        if tasks:
            active_pipelines = session.scalars(
                select(Pipeline).where(
                    Pipeline.project_id == project_id,
                    Pipeline.deleted_at.is_(None),
                    Pipeline.archived_at.is_(None),
                )
            ).all()
            pipeline_by_id = {item.id: item for item in active_pipelines}
            active_tasks = session.scalars(
                select(Task).where(
                    Task.project_id == project_id,
                    Task.deleted_at.is_(None),
                    Task.pipeline_id.in_(pipeline_by_id),
                )
            ).all() if pipeline_by_id else []
            edges = session.scalars(
                select(TaskEdge).where(
                    TaskEdge.project_id == project_id,
                    TaskEdge.deleted_at.is_(None),
                )
            ).all()
            task_readiness = compute_readiness(active_tasks, active_pipelines, edges)

        label_folded = label.casefold() if label else None
        filtered: list[dict[str, Any]] = []
        for row in rows:
            entity_type = str(row["entity_type"])
            entity_id = str(row["entity_id"])
            common = {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "title": str(row["title"] or ""),
                "snippet": str(row["snippet"] or row["title"] or ""),
                "rank": float(row["rank"]),
            }
            if entity_type == "task":
                item = tasks.get(entity_id)
                if item is None or item.deleted_at is not None:
                    continue
                readiness_value = task_readiness.get(
                    item.id,
                    {
                        "readiness": "inconsistent"
                        if item.pipeline_id not in pipeline_by_id
                        else "ready"
                    },
                )["readiness"]
                labels = json.loads(item.labels_json or "[]")
                if status is not None and item.status != status:
                    continue
                if priority is not None and item.priority != priority:
                    continue
                if readiness_state is not None and readiness_value != readiness_state:
                    continue
                if label_folded is not None and label_folded not in {
                    str(value).casefold() for value in labels
                }:
                    continue
                if artifact_type is not None:
                    continue
                common.update(
                    {
                        "key": item.user_key,
                        "kind": item.kind,
                        "status": item.status,
                        "readiness": readiness_value,
                        "priority": item.priority,
                        "labels": labels,
                        "pipeline_id": item.pipeline_id,
                        "parent_id": item.parent_id,
                        "updated_at": jsonable(item.updated_at),
                    }
                )
            elif entity_type == "journal":
                item = journals.get(entity_id)
                if item is None or item.deleted_at is not None:
                    continue
                if any(
                    value is not None
                    for value in (status, priority, readiness_state, label, artifact_type)
                ):
                    continue
                common.update(
                    {
                        "task_id": item.task_id,
                        "entry_type": item.entry_type,
                        "occurred_at": jsonable(item.occurred_at),
                        "updated_at": jsonable(item.updated_at),
                    }
                )
            else:
                item = artifacts.get(entity_id)
                if item is None or item.deleted_at is not None:
                    continue
                if any(
                    value is not None for value in (status, priority, readiness_state, label)
                ):
                    continue
                if artifact_type is not None and item.locator_type != artifact_type:
                    continue
                common.update(
                    {
                        "artifact_type": item.locator_type,
                        "provider": item.provider,
                        "locator": item.locator,
                        "updated_at": jsonable(item.updated_at),
                    }
                )
            filtered.append(common)

        page = filtered[offset : offset + limit]
        return {
            "query": query,
            "results": page,
            "count": len(page),
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "truncated": len(rows) == candidate_limit,
        }

    def agent_context(self, session: Session, project_id: str) -> dict[str, Any]:
        snapshot = self.snapshot(session, project_id)
        snapshot.pop("journals", None)
        snapshot.pop("layouts", None)
        snapshot.pop("viewports", None)
        snapshot["source_references"] = [
            model_dict(item) for item in session.scalars(select(SourceReference).where(SourceReference.project_id == project_id))
        ]
        snapshot["proposal_contract"] = {
            "api_version": API_VERSION,
            "schema_version": SCHEMA_VERSION,
            "actor": "agent",
            "operation_types": sorted(self.semantic_operation_types()),
            "identity_rule": "Prefer monitor UUID/source reference; never merge by title alone.",
            "completion_rule": "Completion requires explicit text, user instruction, or unambiguous result evidence.",
        }
        return snapshot

    @staticmethod
    def semantic_operation_types() -> set[str]:
        return {
            "project.update", "project.archive", "project.trash", "project.restore", "project.relink",
            "planning_profile.update",
            "scan_policy.update", "pipeline.create", "pipeline.update", "pipeline.archive",
            "pipeline.delete", "pipeline.restore", "task.create", "task.update", "task.move",
            "task.delete", "task.restore", "edge.create", "edge.update", "edge.delete",
            "journal.create", "journal.update", "journal.delete", "artifact_root.create",
            "artifact_root.delete", "artifact.create", "artifact.update", "artifact.delete",
            "task_artifact.link", "task_artifact.unlink",
        }

    @staticmethod
    def layout_operation_types() -> set[str]:
        return {"layout.upsert", "layout.delete", "viewport.upsert"}

    @staticmethod
    def _public_journal(item: JournalEntry) -> dict[str, Any]:
        value = model_dict(item)
        value["version"] = value.pop("entity_version")
        return value

    @staticmethod
    def _public_layout(item: TaskLayout) -> dict[str, Any]:
        return {
            "id": item.id, "task_id": item.task_id,
            "parent_id": None if item.scope_id == "root" else item.scope_id,
            "x": item.x, "y": item.y, "version": item.entity_version,
        }

    @staticmethod
    def _public_viewport(item: GraphViewport) -> dict[str, Any]:
        return {
            "id": item.id,
            "parent_id": None if item.scope_id == "root" else item.scope_id,
            "x": item.x, "y": item.y, "zoom": item.zoom,
            "version": item.entity_version,
        }

    def _progress(self, session: Session, project_id: str) -> dict[str, Any]:
        tasks = session.scalars(select(Task).where(Task.project_id == project_id, Task.deleted_at.is_(None))).all()
        pipelines = session.scalars(select(Pipeline).where(Pipeline.project_id == project_id, Pipeline.deleted_at.is_(None))).all()
        edges = session.scalars(select(TaskEdge).where(TaskEdge.project_id == project_id, TaskEdge.deleted_at.is_(None))).all()
        active_pipeline_ids = {pipeline.id for pipeline in pipelines if pipeline.archived_at is None}
        active_tasks = [task for task in tasks if task.pipeline_id in active_pipeline_ids]
        active_pipelines = [pipeline for pipeline in pipelines if pipeline.archived_at is None]
        return self._progress_from(active_tasks, compute_readiness(active_tasks, active_pipelines, edges))

    @staticmethod
    def _progress_from(tasks: list[Task], readiness: dict[str, dict[str, Any]]) -> dict[str, Any]:
        active = [t for t in tasks if t.deleted_at is None]
        parent_ids = {t.parent_id for t in active if t.parent_id}
        leaves = [t for t in active if t.id not in parent_ids and t.status != "dropped"]
        status = Counter(t.status for t in active)
        outcomes = Counter(t.outcome for t in active if t.status == "done")
        nonterminal = [task for task in active if task.status not in {"done", "dropped"}]
        ready_counts = Counter(readiness.get(t.id, {}).get("readiness", "ready") for t in nonterminal)
        return {
            "leaf_total": len(leaves), "leaf_done": sum(t.status == "done" for t in leaves),
            "ready": ready_counts["ready"], "waiting": ready_counts["waiting"],
            "blocked": ready_counts["blocked"], "review": status["review"],
            "by_status": dict(status), "by_outcome": dict(outcomes),
        }

    def _audit(
        self, session: Session, project: Project, actor_type: str, actor_label: str,
        action: str, entity_type: str, entity_id: str, before: Any, after: Any,
        request_id: str, result_revision: int | None = None,
    ) -> str:
        sequence = (session.scalar(select(func.max(AuditEvent.sequence)).where(AuditEvent.project_id == project.id)) or 0) + 1
        event_id = str(uuid4())
        session.add(
            AuditEvent(
                id=event_id, project_id=project.id, sequence=sequence, actor_type=actor_type,
                actor_label=actor_label, action=action, entity_type=entity_type, entity_id=entity_id,
                before_json=canonical_json(before), after_json=canonical_json(after), request_id=request_id,
            )
        )
        session.add(
            OutboxEvent(
                project_id=project.id, event_type=action,
                payload_json=canonical_json({
                    "project_id": project.id,
                    "entity_id": entity_id,
                    "revision": (
                        project.semantic_revision
                        if result_revision is None else result_revision
                    ),
                }),
            )
        )
        return event_id
