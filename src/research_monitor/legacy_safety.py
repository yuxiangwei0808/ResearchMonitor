"""Global safety constraints retained for unbound legacy agent proposals.

The v1 proposal schema stays deliberately permissive for wire compatibility.
This module applies current human-owned policy and projected-state invariants
without changing v1 serialization or fingerprinting.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .graph import compute_readiness, derived_sequence_arcs
from .models import (
    Artifact,
    ArtifactRoot,
    JournalEntry,
    Pipeline,
    PlanningProfile,
    Project,
    ScanPolicy,
    SourceReference,
    Task,
    TaskArtifact,
    TaskEdge,
)
from .preview import SafeOpenError, open_regular_beneath
from .proposal_utils import topological_operations
from .schemas import Operation
from .service import DomainError
from .url_safety import parse_http_url


COMPLETION_FIELDS = {
    "status",
    "outcome",
    "completion_summary",
    "completion_source",
    "completion_actor",
    "completion_override_reason",
}
SUSPICIOUS_QUERY_PARTS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "client_secret",
    "credential",
    "credentials",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
}


def _json_array(value: str) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _path_matches(path: str, pattern: str) -> bool:
    return (
        fnmatchcase(path, pattern)
        or PurePosixPath(path).match(pattern)
        or (pattern.startswith("**/") and fnmatchcase(path, pattern[3:]))
    )


def _normalized_relative(raw: Any, *, code: str) -> str:
    text = str(raw or "").replace("\\", "/").strip()
    value = PurePosixPath(text)
    if not text or value.is_absolute() or ".." in value.parts or "\x00" in text:
        raise DomainError(422, code, "Path must be a safe root-relative path")
    return value.as_posix()


def _suspicious_query_key(value: str) -> bool:
    folded = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    tokens = {token for token in folded.split("_") if token}
    return (
        folded in SUSPICIOUS_QUERY_PARTS
        or any(
            part in folded
            for part in ("access_token", "api_key", "secret", "password", "credential")
        )
        or bool(
            tokens
            & {"auth", "authorization", "bearer", "key", "sig", "signature", "token"}
        )
    )


def _project_root(session: Session, project: Project) -> ArtifactRoot:
    root = session.scalar(
        select(ArtifactRoot).where(
            ArtifactRoot.project_id == project.id,
            ArtifactRoot.is_project_root.is_(True),
        )
    )
    if root is None:
        raise DomainError(500, "project_root_missing", "Project root record is unavailable")
    return root


def _validate_source_reference(
    session: Session,
    project: Project,
    policy: ScanPolicy,
    project_root: ArtifactRoot,
    raw: dict[str, Any],
) -> tuple[str, str, int]:
    reference_id = str(raw.get("monitor_reference_id") or raw.get("id") or "")
    stored = session.get(SourceReference, reference_id) if reference_id else None
    if stored is not None and stored.project_id != project.id:
        raise DomainError(
            422,
            "source_reference_project_mismatch",
            "Source reference belongs to another project",
        )
    if reference_id and stored is None:
        raise DomainError(422, "source_identity_mismatch", "Stored source identity is unavailable")

    supplied_root = str(raw.get("source_root_id") or "")
    supplied_path = str(raw.get("path") or raw.get("source_path") or "")
    root_id = supplied_root or (stored.source_root_id if stored is not None else None) or project_root.id
    source_path = supplied_path or (stored.source_path if stored is not None else "")
    path = _normalized_relative(source_path, code="unsafe_source_reference")
    if stored is not None:
        if supplied_root and supplied_root != (stored.source_root_id or project_root.id):
            raise DomainError(422, "source_identity_mismatch", "Stored source root does not match")
        if supplied_path and path != stored.source_path:
            raise DomainError(422, "source_identity_mismatch", "Stored source path does not match")

    readable = {str(item) for item in _json_array(policy.readable_source_root_ids_json)}
    if root_id not in {project_root.id, *readable}:
        raise DomainError(
            422,
            "source_root_not_readable",
            "Source reference root is not approved for agent reading",
        )
    root = session.get(ArtifactRoot, root_id)
    if root is None or root.project_id != project.id:
        raise DomainError(422, "source_root_unavailable", "Source reference root is unavailable")

    excludes = [str(item) for item in _json_array(policy.exclude_globs_json)]
    includes = [str(item) for item in _json_array(policy.include_globs_json)]
    sensitive = [str(item).casefold() for item in _json_array(policy.sensitive_patterns_json)]
    if any(_path_matches(path, pattern) for pattern in excludes):
        raise DomainError(422, "source_excluded", "Excluded source paths cannot be cited")
    folded = path.casefold()
    components = [part.casefold() for part in PurePosixPath(path).parts]
    if any(
        _path_matches(folded, pattern)
        or any(_path_matches(part, pattern) or pattern in part for part in components)
        for pattern in sensitive
    ):
        raise DomainError(422, "source_sensitive", "Sensitive source paths cannot be cited")
    if includes and not any(_path_matches(path, pattern) for pattern in includes):
        raise DomainError(422, "source_not_included", "Source path is outside include globs")

    stored_root = Path(root.root_path)
    try:
        canonical_root = stored_root.resolve(strict=True)
    except OSError as exc:
        raise DomainError(422, "source_root_unavailable", "Source reference root is unavailable") from exc
    if canonical_root != stored_root or not canonical_root.is_dir():
        raise DomainError(422, "source_root_replaced", "Approved source root was replaced")

    opened = None
    try:
        opened = open_regular_beneath(canonical_root, path)
        if opened.size_bytes > policy.max_text_bytes:
            raise DomainError(422, "source_file_too_large", "Source reference exceeds the per-file limit")
        return root_id, path, opened.size_bytes
    except SafeOpenError as exc:
        code = "source_symlink" if exc.code == "artifact_symlink" else "source_unavailable"
        raise DomainError(
            422,
            code,
            "Source reference must identify an existing regular file without symlinks",
            {"reason": exc.code},
        ) from exc
    finally:
        if opened is not None:
            opened.close()


def _validate_artifact_state(
    session: Session,
    project: Project,
    operation: Operation,
) -> Artifact | None:
    current = session.get(Artifact, str(operation.entity_id or "")) if operation.type == "artifact.update" else None
    kind = str(operation.data.get("kind", current.locator_type if current is not None else "local"))
    locator = str(operation.data.get("locator", current.locator if current is not None else ""))
    root_id = operation.data.get("artifact_root_id", current.root_id if current is not None else None)
    root_id = str(root_id or "") or None

    def reject_tombstone() -> Artifact | None:
        if current is not None:
            if current.project_id == project.id and current.deleted_at is not None:
                raise DomainError(
                    409,
                    "entity_deleted",
                    "Proposal targets a deleted artifact",
                    {"entity_id": current.id},
                )
            return current
        if operation.type != "artifact.create":
            return None
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
            raise DomainError(
                409,
                "entity_deleted",
                "Artifact locator belongs to a deleted artifact",
                {"entity_id": tombstone.id, "field": "locator"},
            )
        return None
    if kind == "url":
        try:
            parsed = parse_http_url(locator)
        except ValueError as exc:
            raise DomainError(422, "unsafe_artifact_url", "Only absolute HTTP(S) artifact URLs are accepted") from exc
        if parsed.username is not None or parsed.password is not None:
            raise DomainError(422, "artifact_url_credentials", "Artifact URLs cannot contain credentials")
        if any(_suspicious_query_key(key) for key, _value in parse_qsl(parsed.query, keep_blank_values=True)):
            raise DomainError(422, "artifact_url_secret", "Artifact URL contains a suspicious credential parameter")
        if root_id is not None:
            raise DomainError(422, "url_with_root", "URL artifacts cannot have an artifact root")
        return reject_tombstone()
    if kind != "local":
        raise DomainError(422, "invalid_artifact_kind", "Artifact kind must be local or url")
    _normalized_relative(locator, code="unsafe_artifact_path")
    root = session.get(ArtifactRoot, root_id or "")
    if root is None or root.project_id != project.id:
        raise DomainError(422, "invalid_artifact_root", "Artifact root is not approved")
    return reject_tombstone()


def _descendants(states: dict[str, dict[str, Any]], root_id: str) -> set[str]:
    result: set[str] = set()
    frontier = [root_id]
    while frontier:
        parent_id = frontier.pop()
        for task_id, state in states.items():
            if task_id not in result and state.get("parent_id") == parent_id:
                result.add(task_id)
                frontier.append(task_id)
    result.discard(root_id)
    return result


def _depth(
    task_id: str,
    states: dict[str, dict[str, Any]],
    cache: dict[str, int],
    visiting: set[str] | None = None,
) -> int:
    if task_id in cache:
        return cache[task_id]
    visiting = set() if visiting is None else visiting
    if task_id in visiting:
        raise DomainError(422, "hierarchy_cycle", "Projected task hierarchy contains a cycle")
    state = states.get(task_id)
    if state is None:
        raise DomainError(422, "invalid_parent", "Projected task hierarchy references a missing task")
    visiting.add(task_id)
    parent_id = state.get("parent_id")
    cache[task_id] = 1 if not parent_id else 1 + _depth(str(parent_id), states, cache, visiting)
    visiting.remove(task_id)
    return cache[task_id]


def validate_legacy_agent_constraints(
    session: Session,
    project: Project,
    operations: Iterable[Operation],
) -> None:
    """Validate current policy against a complete projected legacy change set."""

    values = topological_operations(list(operations))
    profile = session.get(PlanningProfile, project.id)
    policy = session.get(ScanPolicy, project.id)
    if profile is None or policy is None:
        raise DomainError(500, "project_policy_missing", "Project policy is unavailable")

    all_tasks = session.scalars(select(Task).where(Task.project_id == project.id)).all()
    all_states = {
        task.id: {
            "pipeline_id": task.pipeline_id,
            "parent_id": task.parent_id,
            "status": task.status,
            "child_flow_mode": task.child_flow_mode,
            "order_index": task.order_index,
            "created_at": task.created_at,
        }
        for task in all_tasks
    }
    states = {
        task.id: dict(all_states[task.id]) for task in all_tasks if task.deleted_at is None
    }
    all_pipelines = session.scalars(
        select(Pipeline).where(Pipeline.project_id == project.id)
    ).all()
    pipeline_flows = {
        pipeline.id: pipeline.flow_mode
        for pipeline in all_pipelines
    }
    active_pipelines = [
        pipeline
        for pipeline in all_pipelines
        if pipeline.deleted_at is None and pipeline.archived_at is None
    ]
    active_pipeline_ids = {pipeline.id for pipeline in active_pipelines}
    projected_active_pipeline_ids = set(active_pipeline_ids)
    base_tasks = [
        task
        for task in all_tasks
        if task.deleted_at is None and task.pipeline_id in active_pipeline_ids
    ]
    protected_pipelines = {
        str(item) for item in _json_array(profile.protected_pipeline_ids_json)
    }
    protected_tasks = {str(item) for item in _json_array(profile.protected_task_ids_json)}
    for task_id in list(protected_tasks):
        protected_tasks.update(_descendants(all_states, task_id))
    protected_tasks.update(
        task.id for task in all_tasks if task.pipeline_id in protected_pipelines
    )

    new_tasks = {
        str(operation.resolved_entity_id())
        for operation in values
        if operation.type == "task.create" and operation.resolved_entity_id() is not None
    }
    created_artifacts = {
        str(operation.resolved_entity_id())
        for operation in values
        if operation.type == "artifact.create" and operation.resolved_entity_id() is not None
    }
    if len(new_tasks) > profile.max_new_tasks_per_proposal:
        raise DomainError(422, "proposal_task_limit", "Proposal exceeds the planning-profile task limit")

    all_edges = session.scalars(
        select(TaskEdge).where(TaskEdge.project_id == project.id)
    ).all()
    base_edges = [edge for edge in all_edges if edge.deleted_at is None]
    edges = {edge.id: (edge.source_id, edge.target_id) for edge in base_edges}
    edge_endpoints = {edge.id: (edge.source_id, edge.target_id) for edge in all_edges}
    edge_states = {
        edge.id: {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "edge_type": edge.edge_type,
            "enabled": edge.enabled,
            "waived_reason": edge.waived_reason,
        }
        for edge in base_edges
    }
    journals = {
        entry.id: entry.task_id
        for entry in session.scalars(select(JournalEntry).where(JournalEntry.project_id == project.id))
    }
    protected_artifacts = {
        link.artifact_id
        for link in session.scalars(select(TaskArtifact).where(TaskArtifact.project_id == project.id))
        if link.task_id in protected_tasks
    }
    hierarchy_changed: set[str] = set(new_tasks)
    source_files: dict[tuple[str, str], int] = {}
    project_root = _project_root(session, project)
    def remember_source(raw: dict[str, Any]) -> None:
        root_id, path, size = _validate_source_reference(
            session, project, policy, project_root, raw
        )
        source_files[(root_id, path)] = max(source_files.get((root_id, path), 0), size)
    def remember_evidence(item: Any) -> None:
        if not isinstance(item, dict):
            return
        reference_id = str(
            item.get("source_reference_id") or item.get("monitor_reference_id") or ""
        )
        if reference_id:
            remember_source({
                "monitor_reference_id": reference_id,
                "source_root_id": item.get("source_root_id"),
                "path": item.get("path") or item.get("source_path"),
            })
            return
        raw_path = str(item.get("path") or item.get("source_path") or "")
        locator = str(item.get("locator") or "")
        if not raw_path and locator:
            try:
                parsed = urlsplit(locator)
            except ValueError as exc:
                raise DomainError(422, "unsafe_source_reference", "Evidence URL authority is malformed") from exc
            if parsed.scheme:
                try:
                    parsed = parse_http_url(locator)
                except ValueError as exc:
                    raise DomainError(422, "unsafe_source_reference", "Evidence locators accept only local paths or HTTP(S) URLs") from exc
                if parsed.username is not None or parsed.password is not None:
                    raise DomainError(422, "evidence_url_credentials", "Evidence URLs cannot contain credentials")
                if any(
                    _suspicious_query_key(key)
                    for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
                ):
                    raise DomainError(422, "evidence_url_secret", "Evidence URL contains a suspicious credential parameter")
                return
            raw_path = locator.split("#", 1)[0]
        if raw_path:
            remember_source({
                "source_root_id": item.get("source_root_id"),
                "path": raw_path,
            })

    def completion_proved(operation: Operation) -> bool:
        for item in operation.evidence:
            if not isinstance(item, dict) or not str(item.get("summary") or "").strip():
                continue
            kind = str(item.get("kind") or "")
            if kind == "completion_text":
                return True
            if kind != "result_evidence":
                continue
            artifact_id = str(item.get("artifact_id") or "")
            if artifact_id:
                artifact = session.get(Artifact, artifact_id)
                if artifact_id in created_artifacts or (
                    artifact is not None
                    and artifact.project_id == project.id
                    and artifact.deleted_at is None
                ):
                    return True
            reference_id = str(item.get("source_reference_id") or "")
            if reference_id:
                remember_source({"monitor_reference_id": reference_id})
                return True
        return False


    def branch_is_protected(task_id: str) -> bool:
        return task_id in protected_tasks or bool(
            _descendants(states, task_id) & protected_tasks
        )

    def container_is_sequential(pipeline_id: str, parent_id: str | None) -> bool:
        if parent_id:
            parent = states.get(parent_id)
            return parent is not None and parent.get("child_flow_mode") == "sequential"
        return pipeline_flows.get(pipeline_id, "sequential") == "sequential"

    def container_has_protected_branch(
        pipeline_id: str,
        parent_id: str | None,
        *,
        excluded: set[str] | None = None,
    ) -> bool:
        skipped = excluded or set()
        return any(
            task_id not in skipped
            and state.get("pipeline_id") == pipeline_id
            and state.get("parent_id") == parent_id
            and branch_is_protected(task_id)
            for task_id, state in states.items()
        )

    def reject_protected(message: str = "Legacy proposals cannot touch protected research work") -> None:
        raise DomainError(403, "protected_entity", message)

    for operation in values:
        if "completion_override_reason" in operation.data:
            raise DomainError(
                403,
                "human_only_completion_override",
                "Completion overrides are human-only",
            )
        for raw in operation.source_references:
            remember_source(raw)
        for item in operation.evidence:
            remember_evidence(item)

        target_id = str(operation.entity_id or "")
        if operation.type == "pipeline.create":
            pipeline_id = str(operation.resolved_entity_id() or "")
            pipeline_flows[pipeline_id] = str(operation.data.get("flow_mode") or "sequential")
            projected_active_pipeline_ids.add(pipeline_id)

        elif operation.type in {"pipeline.update", "pipeline.archive"}:
            if target_id in protected_pipelines:
                reject_protected("Legacy proposals cannot edit a protected pipeline")
            contains_protected = any(
                task_id in protected_tasks and state.get("pipeline_id") == target_id
                for task_id, state in states.items()
            )
            if contains_protected and (
                operation.type == "pipeline.archive" or "flow_mode" in operation.data
            ):
                reject_protected("Pipeline change would indirectly alter protected tasks")
            if "flow_mode" in operation.data:
                pipeline_flows[target_id] = str(operation.data.get("flow_mode") or "sequential")
            if operation.type == "pipeline.archive":
                projected_active_pipeline_ids.discard(target_id)

        elif operation.type == "task.create":
            task_id = str(operation.resolved_entity_id() or "")
            pipeline_id = str(operation.data.get("pipeline_id") or "")
            parent_id = str(operation.data.get("parent_id") or "") or None
            if pipeline_id in protected_pipelines or (parent_id and parent_id in protected_tasks):
                reject_protected("Legacy proposals cannot create work in a protected subtree")
            if container_is_sequential(pipeline_id, parent_id) and container_has_protected_branch(
                pipeline_id, parent_id
            ):
                reject_protected(
                    "Creating a sequential sibling would alter derived edges involving protected work"
                )
            states[task_id] = {
                "pipeline_id": pipeline_id,
                "parent_id": parent_id,
                "status": str(operation.data.get("status") or "planned"),
                "child_flow_mode": str(
                    operation.data.get("child_flow_mode") or "freeform"
                ),
                "order_index": float(operation.data.get("position") or 0),
                "created_at": project.created_at,
            }
            if states[task_id]["status"] == "done" and not completion_proved(operation):
                raise DomainError(
                    422,
                    "completion_evidence_required",
                    "Unbound completion requires explicit source or result proof",
                )

        elif operation.type in {"task.update", "task.move"}:
            if target_id in protected_tasks:
                reject_protected()
            state = states.get(target_id)
            if state is not None:
                subtree = {target_id, *_descendants(states, target_id)}
                structural = bool({"pipeline_id", "parent_id", "position", "child_flow_mode"} & set(operation.data))
                if structural and subtree & protected_tasks:
                    reject_protected("Task change would indirectly alter a protected descendant")
                parent_id = (
                    str(operation.data.get("parent_id") or "") or None
                    if "parent_id" in operation.data
                    else state.get("parent_id")
                )
                pipeline_id = str(operation.data.get("pipeline_id", state.get("pipeline_id")) or "")
                if parent_id and parent_id in protected_tasks:
                    reject_protected("Legacy proposals cannot move work into a protected subtree")
                if pipeline_id in protected_pipelines:
                    reject_protected("Legacy proposals cannot move work into a protected pipeline")
                if "parent_id" in operation.data and parent_id != state.get("parent_id"):
                    hierarchy_changed.add(target_id)
                if parent_id and parent_id in states:
                    pipeline_id = str(states[parent_id].get("pipeline_id") or pipeline_id)
                old_parent = state.get("parent_id")
                old_pipeline = state.get("pipeline_id")
                changes_container = parent_id != old_parent or pipeline_id != old_pipeline
                previous_status = str(state.get("status") or "planned")
                projected_status = str(operation.data.get("status", previous_status) or "planned")
                changes_sequence_membership = (
                    projected_status != previous_status
                    and "dropped" in {previous_status, projected_status}
                )
                if (
                    "position" in operation.data
                    or changes_container
                    or changes_sequence_membership
                ):
                    for affected_pipeline, affected_parent in {
                        (str(old_pipeline or ""), old_parent),
                        (pipeline_id, parent_id),
                    }:
                        if container_is_sequential(
                            affected_pipeline, affected_parent
                        ) and container_has_protected_branch(
                            affected_pipeline,
                            affected_parent,
                            excluded=subtree,
                        ):
                            reject_protected(
                                "Reordering sequential siblings would alter derived edges involving protected work"
                            )
                state["parent_id"] = parent_id
                state["pipeline_id"] = pipeline_id
                if changes_container:
                    hierarchy_changed.add(target_id)
                if pipeline_id != old_pipeline:
                    for descendant_id in _descendants(states, target_id):
                        states[descendant_id]["pipeline_id"] = pipeline_id
                if "status" in operation.data:
                    state["status"] = str(operation.data.get("status") or "planned")
                if "child_flow_mode" in operation.data:
                    state["child_flow_mode"] = str(
                        operation.data.get("child_flow_mode") or "freeform"
                    )
                if "position" in operation.data:
                    state["order_index"] = float(operation.data["position"])
                completion_touch = state["status"] == "done" and bool(
                    set(operation.data) & COMPLETION_FIELDS
                )
                if completion_touch and not completion_proved(operation):
                    raise DomainError(
                        422,
                        "completion_evidence_required",
                        "Unbound completion requires explicit source or result proof",
                    )

        elif operation.type == "edge.create":
            source_id = str(operation.data.get("source_task_id") or operation.data.get("source_id") or "")
            destination_id = str(operation.data.get("target_task_id") or operation.data.get("target_id") or "")
            if {source_id, destination_id} & protected_tasks:
                reject_protected("Legacy proposals cannot touch edges incident to protected tasks")
            edge_id = str(operation.resolved_entity_id() or "")
            edges[edge_id] = (source_id, destination_id)
            edge_states[edge_id] = {
                "source_id": source_id,
                "target_id": destination_id,
                "edge_type": str(operation.data.get("edge_type") or "dependency"),
                "enabled": not bool(operation.data.get("disabled")),
                "waived_reason": str(operation.data.get("waiver_reason") or ""),
            }

        elif operation.type == "edge.update":
            if set(edge_endpoints.get(target_id, ())) & protected_tasks:
                reject_protected("Legacy proposals cannot touch edges incident to protected tasks")
            edge_state = edge_states.get(target_id)
            if edge_state is not None:
                if "edge_type" in operation.data:
                    edge_state["edge_type"] = str(operation.data["edge_type"])
                if "disabled" in operation.data:
                    edge_state["enabled"] = not bool(operation.data["disabled"])
                if "waiver_reason" in operation.data:
                    edge_state["waived_reason"] = str(operation.data["waiver_reason"])

        elif operation.type == "journal.create":
            if str(operation.data.get("task_id") or "") in protected_tasks:
                reject_protected("Legacy proposals cannot journal protected tasks")

        elif operation.type == "journal.update":
            if journals.get(target_id) in protected_tasks:
                reject_protected("Legacy proposals cannot edit journals on protected tasks")

        elif operation.type in {"artifact.create", "artifact.update"}:
            touched_artifact = _validate_artifact_state(session, project, operation)
            if touched_artifact is not None and touched_artifact.id in protected_artifacts:
                reject_protected("Legacy proposals cannot edit artifacts associated with protected tasks")

        elif operation.type == "task_artifact.link":
            if str(operation.data.get("task_id") or "") in protected_tasks:
                reject_protected("Legacy proposals cannot relink protected tasks")

    projected_pipelines = [
        SimpleNamespace(
            id=pipeline_id,
            flow_mode=pipeline_flows[pipeline_id],
            deleted_at=None,
        )
        for pipeline_id in sorted(projected_active_pipeline_ids)
    ]
    projected_tasks = [
        SimpleNamespace(
            id=task_id,
            pipeline_id=str(state["pipeline_id"]),
            parent_id=state.get("parent_id"),
            status=str(state["status"]),
            deleted_at=None,
            child_flow_mode=str(state["child_flow_mode"]),
            order_index=float(state["order_index"]),
            created_at=state["created_at"],
        )
        for task_id, state in sorted(states.items())
        if state.get("pipeline_id") in projected_active_pipeline_ids
    ]
    projected_edges = [
        SimpleNamespace(
            id=edge_id,
            source_id=str(state["source_id"]),
            target_id=str(state["target_id"]),
            edge_type=str(state["edge_type"]),
            enabled=bool(state["enabled"]),
            waived_reason=str(state["waived_reason"]),
            deleted_at=None,
        )
        for edge_id, state in sorted(edge_states.items())
    ]

    before_readiness = compute_readiness(base_tasks, active_pipelines, base_edges)
    after_readiness = compute_readiness(
        projected_tasks, projected_pipelines, projected_edges
    )
    before_sequence = {
        (arc.source_id, arc.target_id)
        for arc in derived_sequence_arcs(base_tasks, active_pipelines)
    }
    after_sequence = {
        (arc.source_id, arc.target_id)
        for arc in derived_sequence_arcs(projected_tasks, projected_pipelines)
    }
    protected_sequence_changes = sorted(
        edge
        for edge in before_sequence ^ after_sequence
        if set(edge) & protected_tasks
    )
    if protected_sequence_changes:
        raise DomainError(
            403,
            "protected_entity",
            "Legacy changes cannot alter derived sequence edges incident to protected tasks",
            {"sequence_edges": protected_sequence_changes[:100]},
        )

    protected_readiness_changes = sorted(
        task_id
        for task_id in protected_tasks & set(before_readiness)
        if (
            before_readiness[task_id]["readiness"],
            tuple(before_readiness[task_id]["predecessor_ids"]),
            tuple(before_readiness[task_id]["unsatisfied_predecessor_ids"]),
        )
        != (
            after_readiness.get(task_id, {}).get("readiness"),
            tuple(after_readiness.get(task_id, {}).get("predecessor_ids", [])),
            tuple(
                after_readiness.get(task_id, {}).get(
                    "unsatisfied_predecessor_ids", []
                )
            ),
        )
    )
    if protected_readiness_changes:
        raise DomainError(
            403,
            "protected_entity",
            "Legacy changes cannot alter protected task readiness",
            {"task_ids": protected_readiness_changes[:100]},
        )

    if len(source_files) > policy.max_files_per_scan:
        raise DomainError(422, "scan_file_budget_exceeded", "Proposal cites too many source files")
    if sum(source_files.values()) > policy.max_total_text_bytes:
        raise DomainError(422, "scan_text_budget_exceeded", "Proposal source citations exceed the text budget")

    affected = set(new_tasks)
    for task_id in hierarchy_changed:
        affected.add(task_id)
        affected.update(_descendants(states, task_id))
    cache: dict[str, int] = {}
    over_depth = sorted(
        task_id
        for task_id in affected
        if task_id in states and _depth(task_id, states, cache) > profile.max_nesting_depth
    )
    if over_depth:
        raise DomainError(
            422,
            "proposal_depth_limit",
            "Proposal exceeds the planning-profile nesting depth",
            {"task_ids": over_depth, "maximum": profile.max_nesting_depth},
        )
