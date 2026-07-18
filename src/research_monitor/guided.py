"""Trusted guided-request issuance, scoped context, and v2 validation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .contracts import (
    GUIDED_EVIDENCE_KINDS,
    GUIDED_EVIDENCE_FIELDS as EVIDENCE_KEYS,
    GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES,
    GUIDED_EVIDENCE_REQUIRED_FIELDS as EVIDENCE_REQUIRED_FIELDS,
    GUIDED_MODE_CONTRACTS,
    GUIDED_OPERATION_BASES,
    SOURCE_REFERENCE_KEYS,
)
from .graph import compute_readiness, derived_sequence_arcs, descendants
from .models import (
    AgentIntent,
    Artifact,
    ArtifactRoot,
    JournalEntry,
    Pipeline,
    PlanningProfile,
    Project,
    Proposal,
    ScanPolicy,
    TaskSourceReference,
    SourceReference,
    Task,
    TaskArtifact,
    TaskEdge,
    utcnow,
)
from .proposal_utils import topological_operations, validate_agent_operations
from .preview import SafeOpenError, open_regular_beneath
from .schemas import AgentPromptCreate, Operation, ProposalEnvelope
from .serializers import canonical_json, jsonable
from .url_safety import parse_http_url
from .service import (
    DomainError,
    _public_artifact_root,
    _public_edge,
    _public_pipeline,
    _public_planning_profile,
    _public_scan_policy,
    _public_task,
)


INTENT_LIFETIME = timedelta(hours=24)
MAX_CONTEXT_SCOPE_TASKS = 10_000
MAX_CONTEXT_SCOPE_PIPELINES = 2_000
MAX_CONTEXT_INTERNAL_EDGES = 25_000
MAX_CONTEXT_ACTIVE_TASKS = 20_000
MAX_CONTEXT_ACTIVE_EDGES = 50_000
MAX_CONTEXT_BOUNDARY_EDGES = 10_000
SUSPICIOUS_QUERY_PARTS = {
    "access_token", "api_key", "apikey", "auth", "authorization", "bearer",
    "client_secret", "credential", "credentials", "key", "password", "secret",
    "sig", "signature", "token",
}
COMPLETION_FIELDS = {
    "status", "outcome", "completion_summary", "completion_source",
    "completion_actor", "completion_override_reason",
}
SOURCE_EVIDENCE_KINDS = {
    "source_text", "git_metadata", "completion_text", "result_evidence",
    "existing_artifact",
}


def _suspicious_query_key(value: str) -> bool:
    folded = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    tokens = {token for token in folded.split("_") if token}
    return (
        folded in SUSPICIOUS_QUERY_PARTS
        or any(
            part in folded
            for part in (
                "access_token", "api_key", "secret", "password", "credential"
            )
        )
        or bool(
            tokens
            & {"auth", "authorization", "bearer", "key", "sig", "signature", "token"}
        )
    )



def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _json_array(value: str) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _normalized_relative(raw: Any, *, code: str = "unsafe_source_reference") -> str:
    text = str(raw or "").replace("\\", "/").strip()
    value = PurePosixPath(text)
    if not text or value.is_absolute() or ".." in value.parts or "\x00" in text:
        raise DomainError(422, code, "Path must be a safe root-relative path")
    return value.as_posix()


def _collection(items: Iterable[dict[str, Any]], limit: int) -> dict[str, Any]:
    values = list(items)
    return {
        "items": values[:limit],
        "total": len(values),
        "limit": limit,
        "truncated": len(values) > limit,
    }


def _project_root(session: Session, project_id: str) -> ArtifactRoot:
    root = session.scalar(
        select(ArtifactRoot).where(
            ArtifactRoot.project_id == project_id,
            ArtifactRoot.is_project_root.is_(True),
        )
    )
    if root is None:
        raise DomainError(500, "project_root_missing", "Project root record is unavailable")
    return root


def _scope_entities(
    session: Session,
    project: Project,
    scope_type: str,
    scope_id: str | None,
) -> tuple[set[str], set[str], Task | None, Pipeline | None]:
    pipelines = session.scalars(
        select(Pipeline).where(
            Pipeline.project_id == project.id,
            Pipeline.deleted_at.is_(None),
            Pipeline.archived_at.is_(None),
        )
    ).all()
    tasks = session.scalars(
        select(Task).where(Task.project_id == project.id, Task.deleted_at.is_(None))
    ).all()
    active_pipeline_ids = {item.id for item in pipelines}
    active_tasks = [item for item in tasks if item.pipeline_id in active_pipeline_ids]
    if scope_type == "project":
        return (
            {item.id for item in active_tasks},
            active_pipeline_ids,
            None,
            None,
        )
    if scope_type == "pipeline":
        pipeline = session.get(Pipeline, scope_id or "")
        if (
            pipeline is None
            or pipeline.project_id != project.id
            or pipeline.deleted_at is not None
            or pipeline.archived_at is not None
        ):
            raise DomainError(422, "intent_scope_ineligible", "Pipeline scope is unavailable")
        return (
            {item.id for item in active_tasks if item.pipeline_id == pipeline.id},
            {pipeline.id},
            None,
            pipeline,
        )
    task = session.get(Task, scope_id or "")
    if (
        task is None
        or task.project_id != project.id
        or task.deleted_at is not None
        or task.pipeline_id not in active_pipeline_ids
    ):
        raise DomainError(422, "intent_scope_ineligible", "Task scope is unavailable")
    child_map = descendants(active_tasks)
    return (
        {task.id, *child_map.get(task.id, set())},
        {task.pipeline_id},
        task,
        None,
    )


def _project_eligible(project: Project) -> None:
    if project.archived_at is not None or project.trashed_at is not None:
        raise DomainError(422, "project_ineligible", "Archived or trashed projects cannot issue guided requests")
    root = Path(project.root_path)
    try:
        available = root.is_dir() and root.resolve(strict=True) == root
    except (OSError, RuntimeError):
        available = False
    if not available:
        raise DomainError(422, "project_unavailable", "Project root is unavailable or was replaced")


def _intent_prompt(intent: AgentIntent, project: Project) -> str:
    command = (
        "research-monitor agent context "
        f"--project {project.id} --intent {intent.id} --json"
    )
    return (
        "Use the $research-monitor companion skill for this review-only guided request.\n"
        "First run research-monitor version --json and require guided_agent_intents=1, "
        "proposal_contract=2, scoped_agent_context=1, and no_change_results=1.\n"
        f"Then run: {command}\n"
        f"Canonical project root (JSON string): {json.dumps(project.root_path, ensure_ascii=True)}\n"
        f"Requested mode: {intent.workflow_mode}; scope: {intent.scope_type}"
        f"{' ' + intent.scope_id if intent.scope_id else ''}.\n"
        "Inspect only scan-policy-permitted text, do not execute or modify project files, "
        "and submit exactly one bound changes proposal or one no_changes result. "
        "Never apply the proposal."
    )


def public_intent(session: Session, intent: AgentIntent) -> dict[str, Any]:
    project = session.get(Project, intent.project_id)
    if project is None:
        raise DomainError(404, "project_not_found", "Project not found")
    context_command = (
        "research-monitor agent context "
        f"--project {project.id} --intent {intent.id} --json"
    )
    open_drafts = session.scalars(
        select(Proposal)
        .where(
            Proposal.project_id == project.id,
            Proposal.status == "draft",
        )
        .order_by(Proposal.created_at.desc(), Proposal.id)
        .limit(20)
    ).all()
    warnings = (
        [{
            "code": "open_proposal_drafts",
            "message": "This project already has open proposal drafts",
            "proposal_ids": [item.id for item in open_drafts],
        }]
        if open_drafts
        else []
    )
    return {
        "intent_id": intent.id,
        "proposal_request_id": intent.proposal_request_id,
        "prompt_version": "2",
        "project_id": intent.project_id,
        "issued_semantic_revision": intent.issued_semantic_revision,
        "planning_profile_version": intent.planning_profile_version,
        "workflow_mode": intent.workflow_mode,
        "scope_type": intent.scope_type,
        "scope_id": intent.scope_id,
        "allow_completion": intent.allow_completion,
        "instructions": intent.instructions,
        "artifact_locators": _json_array(intent.artifact_locators_json),
        "regenerates_proposal_id": intent.regenerates_proposal_id,
        "expires_at": jsonable(intent.expires_at),
        "consumed_proposal_id": intent.consumed_proposal_id,
        "context_command": context_command,
        "prompt": _intent_prompt(intent, project),
        "disclosure": (
            "Copying sends nothing. Running this prompt in Codex may send the disclosed "
            "monitor context and scan-policy-permitted project text to OpenAI."
        ),
        "warnings": warnings,
    }


def issue_intent(
    service: Any,
    session: Session,
    project_id: str,
    payload: AgentPromptCreate,
) -> dict[str, Any]:
    project = service._project(session, project_id)
    _project_eligible(project)
    profile = session.get(PlanningProfile, project.id)
    if profile is None:
        raise DomainError(500, "planning_profile_missing", "Planning profile is unavailable")
    task_ids, pipeline_ids, scope_task, _pipeline = _scope_entities(
        session,
        project,
        payload.scope_type,
        str(payload.scope_id) if payload.scope_id else None,
    )
    protected_pipelines, protected_tasks = _protected_ids(session, project, profile)
    requested_scope_id = str(payload.scope_id) if payload.scope_id else None
    if (
        payload.scope_type == "pipeline"
        and requested_scope_id in protected_pipelines
    ) or (
        payload.scope_type == "task"
        and requested_scope_id in protected_tasks
    ):
        raise DomainError(
            403,
            "protected_scope",
            "Guided requests cannot target a protected scope",
        )
    if payload.mode == "initialize_structure" and (task_ids or pipeline_ids):
        raise DomainError(
            422, "initialize_requires_empty_project",
            "Initialization is available only when no active pipelines or tasks exist",
        )
    if (
        payload.mode == "expand_task"
        and scope_task is not None
        and scope_task.status in {"done", "dropped"}
    ):
        raise DomainError(
            422,
            "intent_scope_ineligible",
            "A terminal task cannot be expanded",
        )
    if (
        payload.mode == "suggest_next_work"
        and payload.scope_type == "pipeline"
        and not pipeline_ids
    ):
        raise DomainError(422, "intent_scope_ineligible", "Pipeline scope is unavailable")
    artifacts = [item.model_dump(mode="json") for item in payload.artifact_locators]
    for artifact in artifacts:
        _validate_artifact_operation(
            session,
            project,
            Operation(type="artifact.create", data=artifact),
        )
    regeneration: Proposal | None = None
    if payload.regenerate_proposal_id is not None:
        regeneration = session.get(Proposal, str(payload.regenerate_proposal_id))
        if regeneration is None or regeneration.project_id != project.id:
            raise DomainError(404, "proposal_not_found", "Stale proposal was not found")
        if (
            regeneration.proposal_contract_version != "2"
            or regeneration.intent_id is None
        ):
            raise DomainError(
                422,
                "regeneration_lineage_invalid",
                "Only intent-bound guided proposals can be regenerated",
            )
        if regeneration.status not in {"draft", "conflict"}:
            raise DomainError(
                409,
                "proposal_closed",
                "Only an open or conflicted guided proposal can be regenerated",
            )
        if (
            regeneration.status == "draft"
            and regeneration.base_semantic_revision == project.semantic_revision
        ):
            raise DomainError(
                409,
                "proposal_not_stale",
                "This proposal is still current; revise it graphically instead",
            )
        prior_intent = session.get(AgentIntent, regeneration.intent_id)
        if prior_intent is None or prior_intent.project_id != project.id:
            raise DomainError(
                422,
                "regeneration_lineage_invalid",
                "The prior guided request is unavailable",
            )
        requested_scope_id = (
            str(payload.scope_id) if payload.scope_id is not None else None
        )
        immutable_claims_match = (
            payload.mode == prior_intent.workflow_mode
            and payload.scope_type == prior_intent.scope_type
            and requested_scope_id == prior_intent.scope_id
            and payload.allow_completion == prior_intent.allow_completion
            and artifacts == _json_array(prior_intent.artifact_locators_json)
            and payload.instructions == prior_intent.instructions
            and regeneration.workflow_mode == prior_intent.workflow_mode
            and regeneration.scope_type == prior_intent.scope_type
            and regeneration.scope_id == prior_intent.scope_id
        )
        if not immutable_claims_match:
            raise DomainError(
                422,
                "regeneration_claim_mismatch",
                "Regeneration must retain the original mode, scope, permissions, locators, and instructions",
            )
    now = utcnow()
    candidates = session.scalars(
        select(AgentIntent)
        .where(
            AgentIntent.project_id == project.id,
            AgentIntent.issued_semantic_revision == project.semantic_revision,
            AgentIntent.planning_profile_version == profile.entity_version,
            AgentIntent.workflow_mode == payload.mode,
            AgentIntent.scope_type == payload.scope_type,
            AgentIntent.scope_id
            == (str(payload.scope_id) if payload.scope_id is not None else None),
            AgentIntent.consumed_proposal_id.is_(None),
        )
        .order_by(AgentIntent.created_at.desc())
    ).all()
    for candidate in ([] if payload.force_fresh else candidates):
        if (
            _aware(candidate.expires_at) > _aware(now)
            and candidate.instructions == payload.instructions
            and _json_array(candidate.artifact_locators_json) == artifacts
            and candidate.allow_completion == payload.allow_completion
            and candidate.regenerates_proposal_id
            == (str(payload.regenerate_proposal_id) if payload.regenerate_proposal_id else None)
        ):
            return public_intent(session, candidate)
    intent = AgentIntent(
        id=str(uuid4()),
        proposal_request_id=str(uuid4()),
        project_id=project.id,
        issued_semantic_revision=project.semantic_revision,
        planning_profile_version=profile.entity_version,
        workflow_mode=payload.mode,
        scope_type=payload.scope_type,
        scope_id=str(payload.scope_id) if payload.scope_id else None,
        instructions=payload.instructions,
        allow_completion=payload.allow_completion,
        artifact_locators_json=canonical_json(artifacts),
        regenerates_proposal_id=(
            str(payload.regenerate_proposal_id) if payload.regenerate_proposal_id else None
        ),
        expires_at=now + INTENT_LIFETIME,
    )
    session.add(intent)
    session.flush()
    return public_intent(session, intent)


def require_intent(
    session: Session,
    project: Project,
    intent_id: UUID | str | None,
    *,
    require_unconsumed: bool = True,
) -> AgentIntent:
    intent = session.get(AgentIntent, str(intent_id or ""))
    if intent is None or intent.project_id != project.id:
        raise DomainError(404, "intent_not_found", "Guided request intent was not found")
    if _aware(intent.expires_at) <= _aware(utcnow()) and intent.consumed_proposal_id is None:
        raise DomainError(409, "intent_expired", "Guided request expired before submission")
    if require_unconsumed and intent.consumed_proposal_id is not None:
        raise DomainError(
            409, "intent_consumed", "Guided request already produced a proposal",
            {"proposal_id": intent.consumed_proposal_id},
        )
    profile = session.get(PlanningProfile, project.id)
    if (
        project.semantic_revision != intent.issued_semantic_revision
        or profile is None
        or profile.entity_version != intent.planning_profile_version
    ):
        raise DomainError(
            409, "intent_stale", "Project semantics changed after this guided request was issued",
            {
                "intent_revision": intent.issued_semantic_revision,
                "current_revision": project.semantic_revision,
            },
        )
    _project_eligible(project)
    _scope_entities(session, project, intent.scope_type, intent.scope_id)
    return intent


def _path_matches(path: str, pattern: str) -> bool:
    return (
        fnmatchcase(path, pattern)
        or PurePosixPath(path).match(pattern)
        or (pattern.startswith("**/") and fnmatchcase(path, pattern[3:]))
    )


def _validate_source_identity(
    session: Session,
    project: Project,
    policy: ScanPolicy,
    raw: dict[str, Any],
) -> dict[str, Any]:
    unknown = set(raw) - SOURCE_REFERENCE_KEYS
    if unknown:
        raise DomainError(422, "invalid_source_reference", "Source reference has unsupported fields", sorted(unknown))
    path = _normalized_relative(raw.get("path") or raw.get("source_path"))
    if len(path.encode("utf-8")) > 4096:
        raise DomainError(422, "invalid_source_reference", "Source path exceeds 4 KiB")
    root_id = str(raw.get("source_root_id") or "")
    project_root = _project_root(session, project.id)
    readable = {str(value) for value in _json_array(policy.readable_source_root_ids_json)}
    allowed = {project_root.id, *readable}
    if root_id not in allowed:
        raise DomainError(422, "source_root_not_readable", "Source reference root is not approved for agent reading")
    root = session.get(ArtifactRoot, root_id)
    if root is None or root.project_id != project.id:
        raise DomainError(422, "source_root_unavailable", "Source reference root is unavailable")
    includes = [str(value) for value in _json_array(policy.include_globs_json)]
    excludes = [str(value) for value in _json_array(policy.exclude_globs_json)]
    sensitive = [str(value).casefold() for value in _json_array(policy.sensitive_patterns_json)]
    if any(_path_matches(path, pattern) for pattern in excludes):
        raise DomainError(422, "source_excluded", "Excluded source paths cannot be cited")
    folded = path.casefold()
    components = [part.casefold() for part in PurePosixPath(path).parts]
    for pattern in sensitive:
        if (
            _path_matches(folded, pattern)
            or any(_path_matches(part, pattern) or pattern in part for part in components)
        ):
            raise DomainError(422, "source_sensitive", "Sensitive source paths cannot be cited")
    stored_root = Path(root.root_path)
    if includes and not any(_path_matches(path, pattern) for pattern in includes):
        raise DomainError(422, "source_not_included", "Source reference is outside include globs")
    summary = str(raw.get("summary") or "")
    anchor = str(raw.get("anchor") or "")
    fingerprint = str(
        raw.get("content_hash") or raw.get("fingerprint") or ""
    ).casefold()
    opaque_key = str(raw.get("opaque_key") or "")
    if (
        len(summary.encode("utf-8")) > 1000
        or len(anchor.encode("utf-8")) > 500
        or len(opaque_key.encode("utf-8")) > 240
    ):
        raise DomainError(422, "invalid_source_reference", "Source reference fields exceed their limits")
    if fingerprint and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
        raise DomainError(
            422, "invalid_source_hash", "Source content hash must be a SHA-256 digest"
        )
    try:
        canonical_root = stored_root.resolve(strict=True)
    except OSError as exc:
        raise DomainError(422, "source_root_unavailable", "Source reference root is unavailable") from exc
    if canonical_root != stored_root:
        raise DomainError(422, "source_root_replaced", "Approved source root was replaced")
    opened = None
    try:
        opened = open_regular_beneath(canonical_root, path)
        if opened.size_bytes > policy.max_text_bytes:
            raise DomainError(422, "source_file_too_large", "Source reference exceeds the per-file scan limit")
        if fingerprint:
            digest = hashlib.sha256()
            for chunk in opened.iter_bytes():
                digest.update(chunk)
            if digest.hexdigest() != fingerprint:
                raise DomainError(
                    422,
                    "source_hash_mismatch",
                    "Source content hash does not match the cited file",
                )
        else:
            opened.close()
    except SafeOpenError as exc:
        raise DomainError(
            422,
            "source_unavailable",
            "Source reference could not be opened without following symlinks",
            {"reason": exc.code},
        ) from exc
    finally:
        if opened is not None:
            opened.close()
    monitor_reference_id = str(
        raw.get("monitor_reference_id") or raw.get("id") or ""
    )
    if monitor_reference_id:
        stored = session.get(SourceReference, monitor_reference_id)
        if stored is None or stored.project_id != project.id:
            raise DomainError(
                422,
                "source_identity_mismatch",
                "Stored source identity is unavailable",
            )
        expected = (stored.source_root_id, stored.source_path, stored.anchor, stored.opaque_key)
        supplied = (root_id, path, anchor, opaque_key)
        if expected != supplied:
            raise DomainError(
                422,
                "source_identity_mismatch",
                "Stored source identity does not match the supplied citation",
            )
    return {
        "source_root_id": root_id,
        "path": path,
        "anchor": anchor,
        "summary": summary,
        "content_hash": fingerprint,
        **({"monitor_reference_id": monitor_reference_id} if monitor_reference_id else {}),
        **({"opaque_key": opaque_key} if opaque_key else {}),
    }


def _validate_evidence(
    session: Session,
    project: Project,
    intent: AgentIntent,
    raw: dict[str, Any],
    *,
    created_artifact_ids: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DomainError(422, "invalid_v2_evidence", "Guided evidence must be a structured object")
    kind = str(raw.get("kind") or "")
    if kind not in GUIDED_EVIDENCE_KINDS:
        raise DomainError(422, "invalid_v2_evidence", "Unsupported guided evidence kind")
    unknown = set(raw) - EVIDENCE_KEYS[kind]
    if unknown:
        raise DomainError(422, "invalid_v2_evidence", "Evidence has unsupported fields", sorted(unknown))
    missing = EVIDENCE_REQUIRED_FIELDS[kind] - set(raw)
    if missing:
        raise DomainError(
            422,
            "invalid_v2_evidence",
            "Evidence is missing required fields",
            sorted(missing),
        )
    scalar_fields = EVIDENCE_KEYS[kind] - {"kind", "supporting_identities"}
    invalid_scalars = sorted(
        key for key in scalar_fields
        if key in raw and not isinstance(raw[key], str)
    )
    if invalid_scalars:
        raise DomainError(
            422,
            "invalid_v2_evidence",
            "Evidence scalar fields must be strings",
            invalid_scalars,
        )
    if kind == "source_text" and raw.get("anchor") == "":
        raise DomainError(
            422,
            "invalid_source_evidence",
            "Source-text evidence requires a non-empty anchor",
        )
    identity_alternatives = GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES.get(kind, ())
    has_identity = not identity_alternatives or any(
        all(str(raw.get(field) or "").strip() for field in alternative)
        for alternative in identity_alternatives
    )
    if not has_identity:
        if kind in {"completion_text", "result_evidence"}:
            raise DomainError(
                422,
                "invalid_completion_evidence",
                "Completion and result evidence require a verified source identity",
            )
        if kind == "git_metadata":
            raise DomainError(
                422,
                "invalid_git_evidence",
                "Git evidence requires a commit or content hash",
            )
        raise DomainError(
            422, "invalid_v2_evidence", "Evidence requires a stable identity"
        )

    summary = str(raw.get("summary") or "").strip()
    if not summary or len(summary) > 1000:
        raise DomainError(422, "invalid_v2_evidence", "Evidence requires a bounded summary")
    if any(key in raw for key in ("excerpt", "absolute_path", "raw_text", "locator")):
        raise DomainError(422, "invalid_v2_evidence", "Raw excerpts and locators are not accepted")
    if kind == "source_text":
        policy = session.get(ScanPolicy, project.id)
        if policy is None:
            raise DomainError(500, "scan_policy_missing", "Scan policy is unavailable")
        validated = _validate_source_identity(
            session,
            project,
            policy,
            {key: value for key, value in raw.items() if key != "kind"},
        )
        if not validated.get("content_hash"):
            raise DomainError(
                422,
                "invalid_source_evidence",
                "Source-text evidence requires a content hash",
            )
        return {"kind": "source_text", **validated}
    inline_source = bool(raw.get("source_root_id") and raw.get("path"))
    if kind in {"completion_text", "result_evidence"} and inline_source:
        policy = session.get(ScanPolicy, project.id)
        if policy is None:
            raise DomainError(500, "scan_policy_missing", "Scan policy is unavailable")
        validated = _validate_source_identity(
            session,
            project,
            policy,
            {
                key: value
                for key, value in raw.items()
                if key not in {"kind", "artifact_id", "source_reference_id"}
            },
        )
        if not validated.get("content_hash"):
            raise DomainError(
                422,
                "invalid_source_evidence",
                "Completion and result evidence require a verified source content hash",
            )
        if raw.get("artifact_id"):
            artifact_id = str(raw["artifact_id"])
            artifact = session.get(Artifact, artifact_id)
            if (
                artifact_id not in (created_artifact_ids or set())
                and (
                    artifact is None
                    or artifact.project_id != project.id
                    or artifact.deleted_at is not None
                )
            ):
                raise DomainError(422, "invalid_artifact_evidence", "Artifact evidence is unavailable")
        return {
            "kind": kind,
            **validated,
            **(
                {"artifact_id": str(raw["artifact_id"])}
                if raw.get("artifact_id")
                else {}
            ),
        }
    if kind == "user_instruction" and str(raw.get("intent_id") or "") != intent.id:
        raise DomainError(
            422,
            "intent_evidence_mismatch",
            "User-instruction evidence must reference the bound intent",
        )
    if kind == "existing_artifact":
        artifact_id = str(raw.get("artifact_id") or "")
        artifact = session.get(Artifact, artifact_id)
        if (
            artifact is None
            or artifact.project_id != project.id
            or artifact.deleted_at is not None
        ):
            raise DomainError(422, "invalid_artifact_evidence", "Artifact evidence is unavailable")
    if kind == "result_evidence" and raw.get("artifact_id"):
        artifact_id = str(raw.get("artifact_id") or "")
        artifact = session.get(Artifact, artifact_id)
        if (
            artifact_id not in (created_artifact_ids or set())
            and (
                artifact is None
                or artifact.project_id != project.id
                or artifact.deleted_at is not None
            )
        ):
            raise DomainError(422, "invalid_artifact_evidence", "Artifact evidence is unavailable")
    if kind in {"completion_text", "result_evidence"} and raw.get("source_reference_id"):
        reference = session.get(SourceReference, str(raw["source_reference_id"]))
        if reference is None or reference.project_id != project.id:
            raise DomainError(
                422,
                "invalid_source_evidence",
                "Referenced source identity is unavailable",
            )
        policy = session.get(ScanPolicy, project.id)
        if policy is None:
            raise DomainError(500, "scan_policy_missing", "Scan policy is unavailable")
        validated = _validate_source_identity(
            session,
            project,
            policy,
            {
                "monitor_reference_id": reference.id,
                "source_root_id": reference.source_root_id,
                "path": reference.source_path,
                "anchor": reference.anchor,
                "summary": summary,
                "content_hash": str(raw.get("content_hash") or reference.fingerprint),
                "opaque_key": reference.opaque_key,
            },
        )
        return {
            "kind": kind,
            **validated,
            "source_reference_id": reference.id,
            **(
                {"artifact_id": str(raw["artifact_id"])}
                if raw.get("artifact_id")
                else {}
            ),
        }
    if kind in {"completion_text", "result_evidence"}:
        raise DomainError(422, "invalid_completion_evidence", "Completion and result evidence require a verified source identity")
    if kind == "git_metadata":
        policy = session.get(ScanPolicy, project.id)
        if policy is None or not policy.allow_git_metadata:
            raise DomainError(
                422,
                "git_metadata_disabled",
                "Git metadata inspection is disabled",
            )
        if raw.get("path"):
            path = _normalized_relative(raw["path"])
            includes = [str(value) for value in _json_array(policy.include_globs_json)]
            excludes = [str(value) for value in _json_array(policy.exclude_globs_json)]
            sensitive = [
                str(value).casefold()
                for value in _json_array(policy.sensitive_patterns_json)
            ]
            folded = path.casefold()
            components = [
                part.casefold() for part in PurePosixPath(path).parts
            ]
            if any(_path_matches(path, pattern) for pattern in excludes):
                raise DomainError(422, "source_excluded", "Excluded source paths cannot be cited")
            if any(
                _path_matches(folded, pattern)
                or any(
                    _path_matches(part, pattern) or pattern in part
                    for part in components
                )
                for pattern in sensitive
            ):
                raise DomainError(422, "source_sensitive", "Sensitive source paths cannot be cited")
            if includes and not any(_path_matches(path, pattern) for pattern in includes):
                raise DomainError(422, "source_not_included", "Git evidence path is outside include globs")
            raw = {**raw, "path": path}
        commit = str(raw.get("commit") or "").casefold()
        content_hash = str(raw.get("content_hash") or "").casefold()
        if commit and re.fullmatch(r"[0-9a-f]{7,64}", commit) is None:
            raise DomainError(422, "invalid_git_evidence", "Git commit must be a hexadecimal object ID")
        if content_hash and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", content_hash) is None:
            raise DomainError(
                422,
                "invalid_git_evidence",
                "Git content hash must be a SHA-1 or SHA-256 digest",
            )
    if kind == "inference":
        identities = raw.get("supporting_identities")
        if not isinstance(identities, list) or not identities or len(identities) > 20:
            raise DomainError(422, "invalid_inference_evidence", "Inference requires one to twenty supporting identities")
        if any(not isinstance(value, str) or not value or len(value) > 240 for value in identities):
            raise DomainError(422, "invalid_inference_evidence", "Inference identities must be bounded strings")
    return dict(raw)


def _artifact_locator_key(kind: str, root_id: str | None, locator: str) -> str:
    return canonical_json({"kind": kind, "artifact_root_id": root_id, "locator": locator})


def _resolve_intent_locator(
    intent: AgentIntent,
    operation: Operation,
) -> Operation:
    if operation.type != "artifact.create":
        return operation
    locator = str(operation.data.get("locator") or "")
    prefix = "intent-locator:"
    if not locator.startswith(prefix):
        return operation
    supplied_hash = locator[len(prefix):]
    for raw in _json_array(intent.artifact_locators_json):
        kind = str(raw.get("kind") or "local")
        root_id = str(raw.get("artifact_root_id") or "") or None
        candidate = str(raw.get("locator") or "")
        locator_hash = hashlib.sha256(
            _artifact_locator_key(kind, root_id, candidate).encode("utf-8")
        ).hexdigest()
        if supplied_hash == locator_hash:
            return operation.model_copy(
                update={
                    "data": {
                        **operation.data,
                        "locator": candidate,
                        "kind": kind,
                        "artifact_root_id": root_id,
                    }
                }
            )
    raise DomainError(422, "artifact_not_explicit", "Intent locator token is invalid or does not belong to this request")


def _validate_artifact_operation(
    session: Session,
    project: Project,
    operation: Operation,
) -> str:
    kind = str(operation.data.get("kind") or "local")
    locator = str(operation.data.get("locator") or "")
    root_id = str(operation.data.get("artifact_root_id") or "") or None
    if kind == "local":
        _normalized_relative(locator, code="unsafe_artifact_path")
        root = session.get(ArtifactRoot, root_id or "")
        if root is None or root.project_id != project.id:
            raise DomainError(422, "invalid_artifact_root", "Artifact root is not approved")
    elif kind == "url":
        try:
            parsed = parse_http_url(locator)
        except ValueError as exc:
            raise DomainError(422, "unsafe_artifact_url", "Only absolute HTTP(S) artifact URLs are accepted") from exc
        if parsed.username is not None or parsed.password is not None:
            raise DomainError(422, "artifact_url_credentials", "Artifact URLs cannot contain credentials")
        for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
            if _suspicious_query_key(key):
                raise DomainError(422, "artifact_url_secret", "Artifact URL contains a suspicious credential parameter")
        root_id = None
    else:
        raise DomainError(422, "invalid_artifact_kind", "Artifact kind must be local or url")
    return _artifact_locator_key(kind, root_id, locator)


def _protected_ids(
    session: Session,
    project: Project,
    profile: PlanningProfile,
) -> tuple[set[str], set[str]]:
    pipelines = {str(value) for value in _json_array(profile.protected_pipeline_ids_json)}
    protected = {str(value) for value in _json_array(profile.protected_task_ids_json)}
    tasks = session.scalars(select(Task).where(Task.project_id == project.id)).all()
    child_map = descendants(tasks)
    for task_id in list(protected):
        protected.update(child_map.get(task_id, set()))
    protected.update(item.id for item in tasks if item.pipeline_id in pipelines)
    return pipelines, protected


def _task_depth(
    task_id: str,
    by_id: dict[str, Task],
    cache: dict[str, int],
    visiting: set[str] | None = None,
) -> int:
    if task_id in cache:
        return cache[task_id]
    visiting = set() if visiting is None else visiting
    if task_id in visiting:
        raise DomainError(
            422,
            "hierarchy_cycle",
            "Projected task hierarchy contains a cycle",
            {"task_id": task_id},
        )
    task = by_id.get(task_id)
    if task is None:
        raise DomainError(
            422,
            "invalid_parent",
            "Projected task hierarchy references a missing parent",
            {"task_id": task_id},
        )
    visiting.add(task_id)
    cache[task_id] = (
        1
        if not task.parent_id
        else 1 + _task_depth(task.parent_id, by_id, cache, visiting)
    )
    visiting.remove(task_id)
    return cache[task_id]


def prepare_guided_operations(
    session: Session,
    project: Project,
    envelope: ProposalEnvelope,
    *,
    require_bound_request: bool = True,
    require_unconsumed: bool = True,
) -> tuple[AgentIntent, list[Operation], list[dict[str, Any]]]:
    intent = require_intent(
        session, project, envelope.intent_id, require_unconsumed=require_unconsumed
    )
    if require_bound_request and str(envelope.request_id) != intent.proposal_request_id:
        raise DomainError(422, "intent_request_mismatch", "Proposal request UUID is not bound to this intent")
    if envelope.base_semantic_revision != intent.issued_semantic_revision:
        raise DomainError(409, "intent_stale", "Proposal base revision does not match its intent")
    profile = session.get(PlanningProfile, project.id)
    policy = session.get(ScanPolicy, project.id)
    assert profile is not None and policy is not None
    mode_contract = GUIDED_MODE_CONTRACTS[intent.workflow_mode]
    operations = list(envelope.operations)
    validate_agent_operations(
        operations, allow_guided_user_instruction_completion=True
    )
    disallowed = sorted({item.type for item in operations} - set(mode_contract["operations"]))
    if disallowed:
        raise DomainError(403, "guided_mode_operation", "Operation is not allowed in this guided mode", disallowed)
    required = mode_contract.get("required_operation")
    if required:
        matching = [item for item in operations if item.type == required]
        if len(matching) != 1:
            raise DomainError(422, "guided_required_operation", f"{intent.workflow_mode} requires exactly one {required} operation")
    task_scope, pipeline_scope, scope_task, _scope_pipeline = _scope_entities(
        session, project, intent.scope_type, intent.scope_id
    )
    existing_tasks = session.scalars(
        select(Task).where(Task.project_id == project.id, Task.deleted_at.is_(None))
    ).all()
    existing_by_id = {item.id: item for item in existing_tasks}
    protected_pipelines, protected_tasks = _protected_ids(session, project, profile)
    created_tasks: dict[str, Operation] = {}
    created_pipelines: dict[str, Operation] = {}
    created_artifacts: dict[str, Operation] = {}
    create_operation_by_entity: dict[str, str] = {}
    for operation in operations:
        resolved = operation.resolved_entity_id()
        if operation.type == "task.create" and resolved:
            created_tasks[str(resolved)] = operation
        elif operation.type == "pipeline.create" and resolved:
            created_pipelines[str(resolved)] = operation
        elif operation.type == "artifact.create" and resolved:
            created_artifacts[str(resolved)] = operation
        if operation.type.endswith(".create") and resolved:
            create_operation_by_entity[str(resolved)] = str(operation.id)
    if len(created_tasks) > profile.max_new_tasks_per_proposal:
        raise DomainError(422, "proposal_task_limit", "Proposal exceeds the planning-profile task limit")
    inference_tasks = 0
    source_files: dict[tuple[str, str], dict[str, Any]] = {}
    total_source_bytes = 0

    def remember_source(item: dict[str, Any]) -> None:
        root_id = str(item.get("source_root_id") or "")
        path = str(item.get("path") or item.get("source_path") or "")
        if not root_id or not path:
            return
        if not str(item.get("content_hash") or item.get("fingerprint") or ""):
            raise DomainError(
                422,
                "invalid_source_evidence",
                "Intent-bound source identities require a verified content hash",
            )
        source_files[(root_id, path)] = item

    top_evidence = [
        _validate_evidence(session, project, intent, item)
        for item in envelope.evidence
    ]
    top_references = [
        _validate_source_identity(session, project, policy, item)
        for item in envelope.source_references
    ]
    for item in [*top_evidence, *top_references]:
        remember_source(item)
    explicit_locators = {
        _artifact_locator_key(
            str(item.get("kind") or "local"),
            str(item.get("artifact_root_id") or "") or None,
            str(item.get("locator") or ""),
        )
        for item in _json_array(intent.artifact_locators_json)
    }
    warnings: list[dict[str, Any]] = []
    effective: list[Operation] = []
    proposed_journal_origins: set[tuple[str, str]] = set()
    for operation in operations:
        operation = _resolve_intent_locator(intent, operation)
        if operation.basis not in GUIDED_OPERATION_BASES:
            raise DomainError(422, "operation_basis_required", "Every v2 operation requires a supported basis")
        if operation.basis == "inference":
            if profile.inference_policy == "sources_only":
                raise DomainError(422, "inference_forbidden", "Planning profile permits source-evidenced operations only")
            if operation.confidence is None or operation.confidence > 0.79:
                raise DomainError(422, "inference_confidence", "Inferred operations require confidence 0.79 or lower")
            if operation.type == "task.create":
                inference_tasks += 1
        if any(not isinstance(item, dict) for item in operation.evidence):
            raise DomainError(422, "invalid_v2_evidence", "Guided evidence cannot be a string")
        evidence = [
            _validate_evidence(
                session, project, intent, item,
                created_artifact_ids=set(created_artifacts),
            )
            for item in operation.evidence
            if isinstance(item, dict)
        ]
        references = [
            _validate_source_identity(session, project, policy, item)
            for item in operation.source_references
        ]
        for item in [*evidence, *references]:
            remember_source(item)
        evidence_kinds = {str(item.get("kind") or "") for item in evidence}
        if operation.basis == "source_evidence":
            if not references and not (evidence_kinds & SOURCE_EVIDENCE_KINDS):
                raise DomainError(
                    422, "operation_evidence_required",
                    "Source-evidenced operations require supported source evidence or references",
                )
            if evidence_kinds & {"user_instruction", "inference"}:
                raise DomainError(
                    422, "operation_basis_mismatch",
                    "Source-evidenced operations cannot be based on instructions or inference",
                )
        if operation.basis == "user_instruction":
            if not any(item.get("kind") == "user_instruction" for item in evidence):
                raise DomainError(422, "user_instruction_evidence", "User-instruction operations must reference the bound intent")
            if "inference" in evidence_kinds:
                raise DomainError(422, "operation_basis_mismatch", "User-instruction operations cannot contain inference evidence")
        if operation.basis == "inference":
            if "inference" not in evidence_kinds:
                raise DomainError(422, "inference_evidence", "Inferred operations require explicit inference evidence")
            if "user_instruction" in evidence_kinds:
                raise DomainError(422, "operation_basis_mismatch", "Inferred operations cannot be presented as user instructions")
        if "completion_override_reason" in operation.data:
            raise DomainError(403, "human_only_completion_override", "Completion overrides are human-only")
        target_id = str(operation.entity_id or "")
        if target_id and target_id in protected_tasks:
            raise DomainError(403, "protected_entity", "Guided proposals cannot touch a protected task")
        if operation.type == "pipeline.create":
            if intent.workflow_mode not in {"initialize_structure", "suggest_next_work"} or intent.scope_type != "project":
                raise DomainError(403, "guided_scope_violation", "Pipeline creation is outside the guided scope")
        elif operation.type == "task.create":
            pipeline_id = str(operation.data.get("pipeline_id") or "")
            parent_id = str(operation.data.get("parent_id") or "") or None
            created_task_id = str(operation.resolved_entity_id() or "")
            if parent_id and parent_id == created_task_id:
                raise DomainError(
                    422,
                    "hierarchy_cycle",
                    "A task cannot be its own parent",
                )
            if pipeline_id in protected_pipelines or (parent_id and parent_id in protected_tasks):
                raise DomainError(403, "protected_entity", "Guided proposals cannot create work in a protected subtree")
            if intent.workflow_mode in {"initialize_structure", "suggest_next_work"}:
                if parent_id is not None:
                    raise DomainError(422, "guided_scope_violation", "This mode may create top-level tasks only")
                if intent.scope_type == "pipeline" and pipeline_id not in pipeline_scope:
                    raise DomainError(422, "guided_scope_violation", "New task must remain in the selected pipeline")
                if pipeline_id not in pipeline_scope and pipeline_id not in created_pipelines:
                    raise DomainError(422, "guided_scope_violation", "New task pipeline is outside the intent scope")
            elif intent.workflow_mode == "expand_task":
                if parent_id is None or parent_id not in task_scope | set(created_tasks):
                    raise DomainError(422, "guided_scope_violation", "Expanded tasks must be descendants of the selected task")
                if pipeline_id not in pipeline_scope:
                    raise DomainError(422, "guided_scope_violation", "Expanded tasks must remain in the selected pipeline")
            if str(operation.data.get("status") or "planned") != "planned":
                raise DomainError(422, "guided_new_task_status", "Guided planning creates planned tasks only")
            if str(operation.data.get("outcome") or "not_applicable") != "not_applicable":
                raise DomainError(422, "guided_new_task_outcome", "Guided planning creates tasks with not_applicable outcome")
        elif operation.type == "task.update":
            if target_id not in task_scope or (
                intent.workflow_mode == "record_update"
                and target_id != intent.scope_id
            ):
                raise DomainError(422, "guided_scope_violation", "Task update is outside the intent scope")
            allowed_fields = set(mode_contract.get("task_update_fields") or [])
            extra_fields = set(operation.data) - allowed_fields
            if extra_fields:
                raise DomainError(422, "guided_field_violation", "Task update changes fields outside this mode", sorted(extra_fields))
            task = existing_by_id[target_id]
            completion_change = (
                operation.data.get("status") == "done"
                or (task.status == "done" and bool(set(operation.data) & COMPLETION_FIELDS))
            )
            if completion_change:
                if intent.workflow_mode == "record_update" and not intent.allow_completion:
                    raise DomainError(403, "completion_not_authorized", "This record-update intent did not enable completion")
                proof = any(
                    item.get("kind") in {"completion_text", "result_evidence"}
                    or (
                        item.get("kind") == "user_instruction"
                        and intent.allow_completion
                        and str(item.get("intent_id")) == intent.id
                    )
                    for item in evidence
                )
                if not proof:
                    raise DomainError(422, "completion_evidence_required", "Completion requires explicit completion text, result evidence, or an enabled bound instruction")
        elif operation.type == "journal.create":
            task_id = str(operation.data.get("task_id") or "")
            if task_id not in task_scope or task_id in protected_tasks:
                raise DomainError(422, "guided_scope_violation", "Journal target is outside the intent scope")
            data = dict(operation.data)
            if intent.workflow_mode == "record_update":
                if task_id != intent.scope_id:
                    raise DomainError(422, "guided_scope_violation", "Record-update journal must target exactly the selected task")
                data["_origin_key"] = f"prompt:{intent.id}"
            elif intent.workflow_mode == "reconcile_progress":
                journal_sources = [
                    item
                    for item in [*references, *evidence]
                    if item.get("source_root_id") and item.get("path")
                ]
                identities = sorted({
                    canonical_json(
                        {
                            key: reference.get(key)
                            for key in (
                                "source_root_id", "path", "anchor", "opaque_key",
                                "content_hash",
                            )
                        }
                    )
                    for reference in journal_sources
                })
                if not identities:
                    raise DomainError(422, "journal_source_required", "Reconciled journals require stable source references")
                entry_type = str(data.get("entry_type") or "note")
                digest = hashlib.sha256(
                    canonical_json([task_id, entry_type, identities]).encode("utf-8")
                ).hexdigest()
                data["_origin_key"] = f"source:{digest}"
            origin_key = str(data.get("_origin_key") or "")
            origin_identity = (task_id, origin_key)
            if origin_identity in proposed_journal_origins:
                raise DomainError(
                    409,
                    "journal_origin_duplicate",
                    "A journal with this automation origin already exists",
                    {"task_id": task_id, "origin_key": origin_key},
                )
            existing_origin = session.scalar(
                select(JournalEntry.id).where(
                    JournalEntry.project_id == project.id,
                    JournalEntry.task_id == task_id,
                    JournalEntry.origin_key == origin_key,
                )
            )
            if existing_origin is not None:
                raise DomainError(409, "journal_origin_duplicate", "A journal with this automation origin already exists", {"task_id": task_id, "origin_key": origin_key})
            proposed_journal_origins.add(origin_identity)
            operation = operation.model_copy(update={"data": data})
        elif operation.type == "edge.create":
            source_id = str(operation.data.get("source_task_id") or operation.data.get("source_id") or "")
            target_id_value = str(operation.data.get("target_task_id") or operation.data.get("target_id") or "")
            internal = task_scope | set(created_tasks)
            if source_id not in internal or target_id_value not in internal:
                raise DomainError(422, "guided_scope_violation", "Edge endpoints must remain inside the intent scope")
            if {source_id, target_id_value} & protected_tasks:
                raise DomainError(403, "protected_entity", "Guided proposals cannot touch incident edges of protected tasks")
            if operation.data.get("disabled") or str(
                operation.data.get("waiver_reason") or ""
            ).strip():
                raise DomainError(
                    403,
                    "guided_dependency_waiver",
                    "Guided proposals cannot disable or waive dependencies",
                )
            edge_type = str(operation.data.get("edge_type") or "dependency")
            stored_source, stored_target = source_id, target_id_value
            if edge_type == "related" and stored_source > stored_target:
                stored_source, stored_target = stored_target, stored_source
            tombstone = session.scalar(
                select(TaskEdge).where(
                    TaskEdge.project_id == project.id,
                    TaskEdge.source_id == stored_source,
                    TaskEdge.target_id == stored_target,
                    TaskEdge.edge_type == edge_type,
                    TaskEdge.deleted_at.is_not(None),
                )
            )
            if tombstone is not None:
                raise DomainError(
                    409,
                    "guided_restore_forbidden",
                    "Guided proposals cannot restore a deleted edge",
                )
        elif operation.type == "artifact.create":
            locator_key = _validate_artifact_operation(session, project, operation)
            root_id = str(operation.data.get("artifact_root_id") or "") or None
            duplicate_statement = select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.locator_type
                == str(operation.data.get("kind") or "local"),
                Artifact.locator == str(operation.data.get("locator") or ""),
            )
            duplicate_statement = (
                duplicate_statement.where(Artifact.root_id == root_id)
                if root_id
                else duplicate_statement.where(Artifact.root_id.is_(None))
            )
            duplicate = session.scalar(duplicate_statement)
            if duplicate is not None:
                raise DomainError(
                    409,
                    (
                        "guided_restore_forbidden"
                        if duplicate.deleted_at is not None
                        else "artifact_locator_exists"
                    ),
                    "Guided proposals cannot create or restore a duplicate artifact locator",
                )
            if intent.workflow_mode in {"record_update", "link_artifacts"} and locator_key not in explicit_locators:
                raise DomainError(422, "artifact_not_explicit", "Artifact was not explicitly named in the guided request")
        elif operation.type == "task_artifact.link":
            task_id = str(operation.data.get("task_id") or "")
            if task_id not in task_scope or task_id in protected_tasks:
                raise DomainError(422, "guided_scope_violation", "Artifact link target is outside the intent scope")
            if intent.workflow_mode in {"record_update", "link_artifacts"} and task_id != intent.scope_id:
                raise DomainError(422, "guided_scope_violation", "Artifact must link to exactly the selected task")
            artifact_id = str(operation.data.get("artifact_id") or "")
            if artifact_id not in created_artifacts:
                artifact = session.get(Artifact, artifact_id)
                if (
                    artifact is None
                    or artifact.project_id != project.id
                    or artifact.deleted_at is not None
                ):
                    raise DomainError(
                        422,
                        "invalid_artifact",
                        "Artifact link target is unavailable",
                    )
                if intent.workflow_mode in {"record_update", "link_artifacts"}:
                    existing_key = _artifact_locator_key(
                        artifact.locator_type,
                        artifact.root_id,
                        artifact.locator,
                    )
                    if existing_key not in explicit_locators:
                        raise DomainError(
                            422,
                            "artifact_not_explicit",
                            "Existing artifacts must match an explicit locator in the guided request",
                        )
        effective.append(
            operation.model_copy(
                update={"evidence": evidence, "source_references": references}
            )
        )
    if inference_tasks > 5 and profile.inference_policy == "cautious_gaps":
        raise DomainError(422, "inference_task_limit", "Cautious-gap proposals may infer at most five tasks")
    existing_titles: dict[str, list[str]] = {}
    for task in existing_tasks:
        normalized_title = task.title.strip().casefold()
        if normalized_title:
            existing_titles.setdefault(normalized_title, []).append(task.id)
    for operation in effective:
        matches = (
            existing_titles.get(
                str(operation.data.get("title") or "").strip().casefold(), []
            )
            if operation.type == "task.create"
            else []
        )
        if matches:
            warnings.append({"code": "possible_duplicate_task", "operation_id": str(operation.id), "matching_task_ids": sorted(matches), "message": "A title match was found; the server did not merge by title."})
    if len(source_files) > policy.max_files_per_scan:
        raise DomainError(422, "scan_file_budget", "Proposal cites more files than the scan policy permits")
    for reference in source_files.values():
        root = session.get(ArtifactRoot, reference["source_root_id"])
        if root is None or root.project_id != project.id:
            raise DomainError(422, "source_root_unavailable", "Cited source root is unavailable")
        try:
            opened = open_regular_beneath(Path(root.root_path), reference["path"])
        except SafeOpenError as exc:
            raise DomainError(422, "source_unavailable", "Cited source file is unavailable", {"reason": exc.code}) from exc
        try:
            total_source_bytes += opened.size_bytes
        finally:
            opened.close()
    if total_source_bytes > policy.max_total_text_bytes:
        raise DomainError(422, "scan_byte_budget", "Cited source files exceed the aggregate scan budget")
    scan_summary = envelope.scan_summary.model_dump(mode="json")
    files_read = int(scan_summary.get("files_read", 0))
    files_considered = int(scan_summary.get("files_considered", 0))
    text_bytes_read = int(scan_summary.get("text_bytes_read", 0))
    if files_considered < files_read:
        raise DomainError(422, "invalid_scan_summary", "files_considered cannot be lower than files_read")
    if files_read < len(source_files):
        raise DomainError(422, "invalid_scan_summary", "files_read cannot be lower than the number of uniquely cited files")
    if text_bytes_read < total_source_bytes:
        raise DomainError(422, "invalid_scan_summary", "text_bytes_read cannot be lower than the verified bytes needed for cited files")
    if files_considered > policy.max_files_per_scan or files_read > policy.max_files_per_scan:
        raise DomainError(422, "scan_file_budget", "Scan summary exceeds the file budget")
    if text_bytes_read > policy.max_total_text_bytes:
        raise DomainError(422, "scan_byte_budget", "Scan summary exceeds the byte budget")

    by_operation_id = {str(item.id): item for item in effective}
    derived: list[Operation] = []
    reference_fields = (
        "pipeline_id", "parent_id", "source_task_id", "source_id",
        "target_task_id", "target_id", "task_id", "artifact_id",
    )
    for operation in effective:
        prerequisites = {str(value) for value in operation.prerequisite_operation_ids}
        for field in reference_fields:
            reference = str(operation.data.get(field) or "")
            creator = create_operation_by_entity.get(reference)
            if creator and creator != str(operation.id):
                prerequisites.add(creator)
        for item in operation.evidence:
            creator = create_operation_by_entity.get(str(item.get("artifact_id") or "")) if isinstance(item, dict) else None
            if creator and creator != str(operation.id):
                prerequisites.add(creator)
        derived.append(
            operation.model_copy(
                update={
                    "prerequisite_operation_ids": [
                        UUID(value) for value in sorted(prerequisites)
                    ]
                }
            )
        )
    effective = derived
    by_operation_id = {str(item.id): item for item in effective}
    for artifact_id, create in created_artifacts.items():
        links = [
            item for item in effective
            if item.type == "task_artifact.link"
            and str(item.data.get("artifact_id") or "") == artifact_id
        ]
        if not links:
            raise DomainError(422, "artifact_link_required", "Every guided artifact creation must be linked in scope")
        if create.atomic_group_id is None or any(
            item.atomic_group_id != create.atomic_group_id for item in links
        ):
            raise DomainError(422, "artifact_atomic_group", "Artifact creation and its task link must share one atomic group")
    ordered_effective = topological_operations(effective)
    all_by_id = dict(existing_by_id)
    synthetic: dict[str, Task] = {}
    for task_id, operation in created_tasks.items():
        synthetic[task_id] = Task(
            id=task_id,
            project_id=project.id,
            pipeline_id=str(operation.data["pipeline_id"]),
            parent_id=str(operation.data.get("parent_id") or "") or None,
            title=str(operation.data.get("title") or ""),
        )
    all_by_id.update(synthetic)
    depth_cache: dict[str, int] = {}
    for task_id in synthetic:
        if _task_depth(task_id, all_by_id, depth_cache) > profile.max_nesting_depth:
            raise DomainError(422, "proposal_depth_limit", "New task exceeds the planning-profile nesting limit")
    active_pipelines = session.scalars(
        select(Pipeline).where(
            Pipeline.project_id == project.id,
            Pipeline.deleted_at.is_(None),
            Pipeline.archived_at.is_(None),
        )
    ).all()
    active_pipeline_ids = {item.id for item in active_pipelines}
    base_tasks = [
        item for item in existing_tasks if item.pipeline_id in active_pipeline_ids
    ]
    projected_pipelines: list[Any] = list(active_pipelines)
    for operation in ordered_effective:
        if operation.type == "pipeline.create":
            projected_pipelines.append(
                SimpleNamespace(
                    id=str(operation.resolved_entity_id()),
                    flow_mode=str(operation.data.get("flow_mode") or "sequential"),
                    deleted_at=None,
                    archived_at=None,
                )
            )
    projected_tasks: list[Any] = [
        SimpleNamespace(
            id=item.id,
            pipeline_id=item.pipeline_id,
            parent_id=item.parent_id,
            status=item.status,
            deleted_at=None,
            child_flow_mode=item.child_flow_mode,
            order_index=item.order_index,
            created_at=item.created_at,
        )
        for item in base_tasks
    ]
    projected_by_id = {item.id: item for item in projected_tasks}
    for operation in ordered_effective:
        if operation.type == "task.update" and operation.entity_id:
            task = projected_by_id.get(str(operation.entity_id))
            if task is not None:
                if "status" in operation.data:
                    task.status = str(operation.data["status"])
                if "child_flow_mode" in operation.data:
                    task.child_flow_mode = str(operation.data["child_flow_mode"])
        elif operation.type == "task.create":
            raw_position = operation.data.get("position")
            if raw_position is None:
                sibling_positions = [
                    item.order_index
                    for item in projected_tasks
                    if (
                        item.pipeline_id == str(operation.data.get("pipeline_id") or "")
                        and item.parent_id
                        == (str(operation.data.get("parent_id") or "") or None)
                    )
                ]
                order_index = max(sibling_positions, default=0.0) + 1.0
            else:
                order_index = float(raw_position)
            task = SimpleNamespace(
                id=str(operation.resolved_entity_id()),
                pipeline_id=str(operation.data.get("pipeline_id") or ""),
                parent_id=str(operation.data.get("parent_id") or "") or None,
                status=str(operation.data.get("status") or "planned"),
                deleted_at=None,
                child_flow_mode=str(operation.data.get("child_flow_mode") or "freeform"),
                order_index=order_index,
                created_at=utcnow(),
            )
            projected_tasks.append(task)
            projected_by_id[task.id] = task
    base_edges = session.scalars(
        select(TaskEdge).where(
            TaskEdge.project_id == project.id,
            TaskEdge.deleted_at.is_(None),
        )
    ).all()
    projected_edges: list[Any] = list(base_edges)
    for operation in ordered_effective:
        if operation.type == "edge.create":
            projected_edges.append(
                SimpleNamespace(
                    id=str(operation.resolved_entity_id()),
                    source_id=str(operation.data.get("source_task_id") or operation.data.get("source_id") or ""),
                    target_id=str(operation.data.get("target_task_id") or operation.data.get("target_id") or ""),
                    edge_type=str(operation.data.get("edge_type") or "dependency"),
                    enabled=not bool(operation.data.get("disabled")),
                    waived_reason=str(operation.data.get("waiver_reason") or ""),
                    deleted_at=None,
                )
            )
    before_readiness = compute_readiness(base_tasks, active_pipelines, base_edges)
    after_readiness = compute_readiness(projected_tasks, projected_pipelines, projected_edges)
    before_sequence = {
        (arc.source_id, arc.target_id)
        for arc in derived_sequence_arcs(base_tasks, active_pipelines)
    }
    after_sequence = {
        (arc.source_id, arc.target_id)
        for arc in derived_sequence_arcs(projected_tasks, projected_pipelines)
    }
    protected_sequence_changes = sorted(
        edge for edge in before_sequence ^ after_sequence
        if set(edge) & protected_tasks
    )
    if protected_sequence_changes:
        raise DomainError(
            403,
            "protected_entity",
            "Guided changes cannot alter derived sequence edges incident to protected tasks",
            {"sequence_edges": protected_sequence_changes[:100]},
        )
    protected_readiness_changes = sorted(
        task_id for task_id in protected_tasks & set(before_readiness)
        if before_readiness[task_id]["readiness"]
        != after_readiness.get(task_id, {}).get("readiness")
    )
    if protected_readiness_changes:
        raise DomainError(403, "protected_entity", "Guided changes cannot alter protected task readiness", {"task_ids": protected_readiness_changes[:100]})
    changed_outside = sorted(
        task_id for task_id in set(before_readiness) - task_scope
        if before_readiness[task_id]["readiness"] != after_readiness.get(task_id, {}).get("readiness")
    )
    if changed_outside:
        warnings.append({"code": "out_of_scope_readiness_changed", "task_ids": changed_outside[:100], "count": len(changed_outside), "truncated": len(changed_outside) > 100, "message": "Allowed in-scope changes alter readiness outside the selected scope."})
    return intent, effective, warnings


def validate_top_level_v2_evidence(
    session: Session,
    project: Project,
    intent: AgentIntent,
    envelope: ProposalEnvelope,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    policy = session.get(ScanPolicy, project.id)
    if (
        envelope.result_kind == "no_changes"
        and intent.workflow_mode == "record_update"
    ):
        raise DomainError(
            422, "guided_required_operation", "record_update requires one journal entry"
        )
    assert policy is not None
    evidence = [
        _validate_evidence(session, project, intent, item)
        for item in envelope.evidence
    ]
    references = [
        _validate_source_identity(session, project, policy, item)
        for item in envelope.source_references
    ]
    profile = session.get(PlanningProfile, project.id)
    if profile is None:
        raise DomainError(500, "planning_profile_missing", "Planning profile is unavailable")
    if (
        profile.inference_policy == "sources_only"
        and any(item.get("kind") == "inference" for item in evidence)
    ):
        raise DomainError(422, "inference_forbidden", "Planning profile permits source-evidenced operations only")
    source_files: dict[tuple[str, str], dict[str, Any]] = {}
    for item in [*evidence, *references]:
        root_id = str(item.get("source_root_id") or "")
        path = str(item.get("path") or item.get("source_path") or "")
        if not root_id or not path:
            continue
        if not str(item.get("content_hash") or item.get("fingerprint") or ""):
            raise DomainError(422, "invalid_source_evidence", "Intent-bound source identities require a verified content hash")
        source_files[(root_id, path)] = item
    summary = envelope.scan_summary.model_dump(mode="json")
    if not isinstance(summary, dict) or len(summary) > 20:
        raise DomainError(422, "invalid_scan_summary", "Scan summary must be a bounded object")
    allowed = {"files_considered", "files_read", "text_bytes_read", "truncated", "limitations"}
    if set(summary) - allowed:
        raise DomainError(422, "invalid_scan_summary", "Scan summary contains unsupported fields")
    for key in ("files_considered", "files_read", "text_bytes_read"):
        value = summary.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DomainError(
                422,
                "invalid_scan_summary",
                f"{key} must be a nonnegative integer",
            )
    if not isinstance(summary.get("truncated", False), bool):
        raise DomainError(422, "invalid_scan_summary", "truncated must be a boolean")
    limitations = summary.get("limitations", [])
    if (
        not isinstance(limitations, list)
        or len(limitations) > 20
        or any(
            not isinstance(item, str) or not item or len(item) > 500
            for item in limitations
        )
    ):
        raise DomainError(
            422,
            "invalid_scan_summary",
            "limitations must contain at most twenty bounded strings",
        )
    if summary.get("files_considered", 0) < summary.get("files_read", 0):
        raise DomainError(422, "invalid_scan_summary", "files_considered cannot be lower than files_read")
    if summary.get("files_read", 0) < len(source_files):
        raise DomainError(422, "invalid_scan_summary", "files_read cannot be lower than the number of uniquely cited files")
    cited_bytes = 0
    for reference in source_files.values():
        root = session.get(ArtifactRoot, reference["source_root_id"])
        if root is None or root.project_id != project.id:
            raise DomainError(422, "source_root_unavailable", "Cited source root is unavailable")
        try:
            opened = open_regular_beneath(Path(root.root_path), reference["path"])
        except SafeOpenError as exc:
            raise DomainError(422, "source_unavailable", "Cited source file is unavailable", {"reason": exc.code}) from exc
        try:
            cited_bytes += opened.size_bytes
        finally:
            opened.close()
    if summary.get("text_bytes_read", 0) < cited_bytes:
        raise DomainError(422, "invalid_scan_summary", "text_bytes_read cannot be lower than verified cited-file bytes")
    if len(source_files) > policy.max_files_per_scan:
        raise DomainError(422, "scan_file_budget", "Cited sources exceed the file budget")
    if cited_bytes > policy.max_total_text_bytes:
        raise DomainError(422, "scan_byte_budget", "Cited sources exceed the byte budget")
    if (
        summary.get("files_considered", 0) > policy.max_files_per_scan
        or summary.get("files_read", 0) > policy.max_files_per_scan
    ):
        raise DomainError(422, "scan_file_budget", "Scan summary exceeds the file budget")
    if summary.get("text_bytes_read", 0) > policy.max_total_text_bytes:
        raise DomainError(422, "scan_byte_budget", "Scan summary exceeds the byte budget")
    return evidence, references


def _redacted_artifact(session: Session, artifact: Artifact) -> dict[str, Any]:
    canonical = _artifact_locator_key(artifact.locator_type, artifact.root_id, artifact.locator)
    locator_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    display = artifact.locator
    redacted = False
    if artifact.locator_type == "url":
        try:
            parsed = parse_http_url(artifact.locator)
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            display = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            redacted = display != artifact.locator
        except ValueError:
            display = "[redacted-invalid-url]"
            redacted = True
    policy = session.get(ScanPolicy, artifact.project_id)
    sensitive = [
        str(value).casefold()
        for value in _json_array(policy.sensitive_patterns_json if policy else "[]")
    ]
    if artifact.locator_type == "local" and any(
        value
        and (
            _path_matches(artifact.locator.casefold(), value)
            or any(value in part.casefold() for part in PurePosixPath(artifact.locator).parts)
        )
        for value in sensitive
    ):
        display = "[redacted-sensitive-local-locator]"
        redacted = True
    return {
        "id": artifact.id,
        "kind": artifact.locator_type,
        "artifact_root_id": artifact.root_id,
        "locator": f"artifact-locator:{locator_hash}" if redacted else display,
        "display_locator": display,
        "redacted": redacted,
        "locator_hash": locator_hash,
        "provider": artifact.provider,
        "label": artifact.label,
        "version": artifact.entity_version,
    }



def _redacted_explicit_locator(
    session: Session,
    project_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    kind = str(raw.get("kind") or "local")
    root_id = str(raw.get("artifact_root_id") or "") or None
    locator = str(raw.get("locator") or "")
    locator_hash = hashlib.sha256(
        _artifact_locator_key(kind, root_id, locator).encode("utf-8")
    ).hexdigest()
    display = locator
    redacted = False
    if kind == "url":
        try:
            parsed = parse_http_url(locator)
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            display = urlunsplit((parsed.scheme, host, parsed.path, "", ""))
            redacted = display != locator
        except ValueError:
            display = "[redacted-invalid-url]"
            redacted = True
    else:
        policy = session.get(ScanPolicy, project_id)
        sensitive = [
            str(value).casefold()
            for value in _json_array(
                policy.sensitive_patterns_json if policy else "[]"
            )
        ]
        folded = locator.casefold()
        components = [part.casefold() for part in PurePosixPath(locator).parts]
        if any(
            pattern
            and (
                _path_matches(folded, pattern)
                or any(
                    _path_matches(part, pattern) or pattern in part
                    for part in components
                )
            )
            for pattern in sensitive
        ):
            display = "[redacted-sensitive-local-locator]"
            redacted = True
    return {
        "kind": kind,
        "artifact_root_id": root_id,
        "locator": f"intent-locator:{locator_hash}" if redacted else display,
        "display_locator": display,
        "locator_hash": locator_hash,
        "redacted": redacted,
        "provider": str(raw.get("provider") or ""),
        "label": str(raw.get("label") or ""),
    }


def _source_path_visible(
    policy: ScanPolicy,
    readable_root_ids: set[str],
    source_root_id: str,
    source_path: str,
) -> bool:
    if source_root_id not in readable_root_ids:
        return False
    try:
        path = _normalized_relative(source_path)
    except DomainError:
        return False
    excludes = [str(value) for value in _json_array(policy.exclude_globs_json)]
    sensitive = [str(value).casefold() for value in _json_array(policy.sensitive_patterns_json)]
    folded = path.casefold()
    components = [part.casefold() for part in PurePosixPath(path).parts]
    if any(_path_matches(path, pattern) for pattern in excludes):
        return False
    if any(
        _path_matches(folded, pattern)
        or any(_path_matches(part, pattern) or pattern in part for part in components)
        for pattern in sensitive
    ):
        return False
    includes = [str(value) for value in _json_array(policy.include_globs_json)]
    return not includes or any(_path_matches(path, pattern) for pattern in includes)


def _source_index_visible(
    policy: ScanPolicy,
    readable_root_ids: set[str],
    source: SourceReference,
) -> bool:
    return _source_path_visible(
        policy, readable_root_ids, str(source.source_root_id or ""), source.source_path
    )


def _v2_contract_schemas() -> tuple[dict[str, Any], dict[str, Any]]:
    required_fields = EVIDENCE_REQUIRED_FIELDS

    def string_schema(field: str, kind: str) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": "string"}
        if field == "summary":
            schema["maxLength"] = 1000
            if kind != "source_reference":
                schema["minLength"] = 1
        if field in {"path", "source_path"}:
            schema.update({"minLength": 1, "maxLength": 4096, "pattern": r"^(?!/)(?!.*\\)(?!.*(?:^|/)\.\.?(?:/|$)).+$"})
        if field == "anchor":
            schema["maxLength"] = 500
            if kind != "source_reference":
                schema["minLength"] = 1
        if field == "opaque_key":
            schema.update({"minLength": 1, "maxLength": 240})
        if field.endswith("_id") or field in {"artifact_id", "intent_id", "id"}:
            schema["format"] = "uuid"
        if field == "content_hash":
            schema["pattern"] = "^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$" if kind == "git_metadata" else "^[0-9a-fA-F]{64}$"
        if field == "fingerprint":
            schema["pattern"] = "^[0-9a-f]{64}$"
        if field == "commit" and kind == "git_metadata":
            schema["pattern"] = "^[0-9a-fA-F]{7,64}$"
        return schema

    evidence_variants: list[dict[str, Any]] = []
    for kind, fields in EVIDENCE_KEYS.items():
        properties = {
            field: string_schema(field, kind)
            for field in fields
            if field not in {"kind", "supporting_identities"}
        }
        properties["kind"] = {"const": kind, "type": "string"}
        if "supporting_identities" in fields:
            properties["supporting_identities"] = {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1, "maxLength": 240},
            }
        variant: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": sorted(required_fields[kind]),
        }
        identity_alternatives = GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES.get(
            kind, ()
        )
        if identity_alternatives:
            variant["anyOf"] = [
                {"required": sorted(alternative)}
                for alternative in identity_alternatives
            ]
        evidence_variants.append(variant)

    source_reference_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            field: string_schema(field, "source_reference") for field in SOURCE_REFERENCE_KEYS
        },
        "required": ["source_root_id", "path", "content_hash"],
    }
    operation_schema = Operation.model_json_schema()
    operation_schema["required"] = sorted(
        {*operation_schema.get("required", []), "basis"}
    )
    operation_schema["properties"]["basis"] = {
        "type": "string",
        "enum": sorted(GUIDED_OPERATION_BASES),
    }
    operation_schema["properties"]["evidence"]["items"] = {
        "oneOf": evidence_variants
    }
    operation_schema["properties"]["source_references"]["items"] = (
        source_reference_schema
    )
    envelope_schema = ProposalEnvelope.model_json_schema()
    envelope_schema["required"] = sorted(
        {
            *envelope_schema.get("required", []),
            "proposal_contract_version", "intent_id", "result_kind",
            "scan_summary",
        }
    )
    envelope_schema["properties"]["proposal_contract_version"] = {
        "const": "2", "type": "string",
    }
    envelope_schema["properties"]["intent_id"] = {
        "format": "uuid", "type": "string",
    }
    envelope_schema["properties"]["evidence"]["items"] = {
        "oneOf": evidence_variants
    }
    envelope_schema["properties"]["source_references"]["items"] = (
        source_reference_schema
    )
    envelope_schema.setdefault("$defs", {})["Operation"] = {
        key: value for key, value in operation_schema.items() if key != "$defs"
    }
    return envelope_schema, operation_schema


def scoped_agent_context(
    service: Any,
    session: Session,
    project_id: str,
    intent_id: str,
) -> dict[str, Any]:
    project = service._project(session, project_id)
    intent = require_intent(session, project, intent_id, require_unconsumed=False)
    profile = session.get(PlanningProfile, project.id)
    policy = session.get(ScanPolicy, project.id)
    assert profile is not None and policy is not None
    scope_tasks, scope_pipelines, _task, _pipeline = _scope_entities(
        session, project, intent.scope_type, intent.scope_id
    )
    if (
        len(scope_tasks) > MAX_CONTEXT_SCOPE_TASKS
        or len(scope_pipelines) > MAX_CONTEXT_SCOPE_PIPELINES
    ):
        raise DomainError(
            413,
            "context_scope_too_large",
            "The selected semantic scope is too large; choose a narrower pipeline or task scope",
            {
                "task_count": len(scope_tasks),
                "task_limit": MAX_CONTEXT_SCOPE_TASKS,
                "pipeline_count": len(scope_pipelines),
                "pipeline_limit": MAX_CONTEXT_SCOPE_PIPELINES,
                "recommended_action": "narrow_scope",
            },
        )
    pipelines = session.scalars(
        select(Pipeline).where(
            Pipeline.project_id == project.id,
            Pipeline.deleted_at.is_(None),
            Pipeline.archived_at.is_(None),
        ).order_by(Pipeline.order_index, Pipeline.id)
    ).all()
    tasks = session.scalars(
        select(Task).where(Task.project_id == project.id, Task.deleted_at.is_(None))
        .order_by(Task.order_index, Task.id)
    ).all()
    active_pipeline_ids = {item.id for item in pipelines}
    active_tasks = [item for item in tasks if item.pipeline_id in active_pipeline_ids]
    edges = session.scalars(
        select(TaskEdge).where(
            TaskEdge.project_id == project.id,
            TaskEdge.deleted_at.is_(None),
        )
    ).all()
    if (
        len(active_tasks) > MAX_CONTEXT_ACTIVE_TASKS
        or len(edges) > MAX_CONTEXT_ACTIVE_EDGES
    ):
        raise DomainError(
            413,
            "context_graph_too_large",
            "The active project graph is too large for a complete agent context",
            {
                "active_task_count": len(active_tasks),
                "active_task_limit": MAX_CONTEXT_ACTIVE_TASKS,
                "active_edge_count": len(edges),
                "active_edge_limit": MAX_CONTEXT_ACTIVE_EDGES,
                "recommended_action": "reduce_project_graph_or_narrow_scope",
            },
        )
    readiness = compute_readiness(active_tasks, pipelines, edges)
    sequence_arcs = derived_sequence_arcs(active_tasks, pipelines)
    by_id = {item.id: item for item in active_tasks}
    ancestors: set[str] = set()
    for task_id in scope_tasks:
        parent_id = by_id.get(task_id).parent_id if by_id.get(task_id) else None
        while parent_id and parent_id not in ancestors and parent_id not in scope_tasks:
            ancestors.add(parent_id)
            parent_id = by_id.get(parent_id).parent_id if by_id.get(parent_id) else None
    boundary_edges = [
        item for item in edges
        if (item.source_id in scope_tasks) != (item.target_id in scope_tasks)
    ]
    internal_sequence_edges = [
        item
        for item in sequence_arcs
        if item.source_id in scope_tasks and item.target_id in scope_tasks
    ]
    internal_explicit_count = sum(
        1
        for item in edges
        if item.source_id in scope_tasks and item.target_id in scope_tasks
    )
    internal_edge_count = internal_explicit_count + len(internal_sequence_edges)
    if internal_edge_count > MAX_CONTEXT_INTERNAL_EDGES:
        raise DomainError(
            413,
            "context_scope_too_large",
            "The selected semantic scope has too many internal edges; choose a narrower scope",
            {
                "edge_count": internal_edge_count,
                "edge_limit": MAX_CONTEXT_INTERNAL_EDGES,
                "recommended_action": "narrow_scope",
            },
        )
    boundary_sequence_edges = [
        item
        for item in sequence_arcs
        if (item.source_id in scope_tasks) != (item.target_id in scope_tasks)
    ]
    boundary_edge_count = len(boundary_edges) + len(boundary_sequence_edges)
    if boundary_edge_count > MAX_CONTEXT_BOUNDARY_EDGES:
        raise DomainError(
            413,
            "context_scope_too_large",
            "The selected scope has too many dependency boundary edges",
            {
                "boundary_edge_count": boundary_edge_count,
                "boundary_edge_limit": MAX_CONTEXT_BOUNDARY_EDGES,
                "recommended_action": "narrow_scope",
            },
        )
    boundary_task_ids = {
        value
        for edge in boundary_edges
        for value in (edge.source_id, edge.target_id)
        if value not in scope_tasks
    }
    boundary_task_ids.update(
        value
        for edge in boundary_sequence_edges
        for value in (edge.source_id, edge.target_id)
        if value not in scope_tasks
    )
    selected_tasks = [
        _public_task(item, readiness.get(item.id))
        for item in active_tasks if item.id in scope_tasks
    ]
    roots = session.scalars(
        select(ArtifactRoot).where(ArtifactRoot.project_id == project.id)
        .order_by(ArtifactRoot.is_project_root.desc(), ArtifactRoot.alias, ArtifactRoot.id)
    ).all()
    project_root = _project_root(session, project.id)
    readable_ids = {
        project_root.id,
        *{str(value) for value in _json_array(policy.readable_source_root_ids_json)},
    }
    source_rows: list[SourceReference] = []
    source_visible_total = 0
    source_stream = session.scalars(
        select(SourceReference).where(SourceReference.project_id == project.id)
        .order_by(SourceReference.source_root_id, SourceReference.source_path, SourceReference.anchor, SourceReference.id)
    ).yield_per(500)
    for item in source_stream:
        if not _source_index_visible(policy, readable_ids, item):
            continue
        source_visible_total += 1
        if len(source_rows) < 2_000:
            source_rows.append(item)
    task_source_rows: list[TaskSourceReference] = []
    task_source_total = 0
    task_source_stream = session.execute(
        select(TaskSourceReference, SourceReference)
        .join(
            SourceReference,
            SourceReference.id == TaskSourceReference.source_reference_id,
        )
        .where(TaskSourceReference.project_id == project.id)
        .order_by(
                TaskSourceReference.task_id,
                TaskSourceReference.source_reference_id,
                TaskSourceReference.id,
        )
    ).yield_per(500)
    for association, source in task_source_stream:
        if not _source_index_visible(policy, readable_ids, source):
            continue
        task_source_total += 1
        if len(task_source_rows) < 5_000:
            task_source_rows.append(association)
    artifact_total = int(session.scalar(
        select(func.count()).select_from(Artifact).where(
            Artifact.project_id == project.id, Artifact.deleted_at.is_(None)
        )
    ) or 0)
    journal_total = int(session.scalar(
        select(func.count()).select_from(JournalEntry).where(JournalEntry.project_id == project.id)
    ) or 0)
    link_total = int(session.scalar(
        select(func.count()).select_from(TaskArtifact).where(TaskArtifact.project_id == project.id)
    ) or 0)

    artifacts = session.scalars(
        select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.deleted_at.is_(None),
        ).order_by(Artifact.id).limit(5_000)
    ).all()
    journals = session.scalars(
        select(JournalEntry).where(JournalEntry.project_id == project.id)
        .order_by(JournalEntry.occurred_at.desc(), JournalEntry.id).limit(5_000)
    ).all()
    links = session.scalars(
        select(TaskArtifact).where(TaskArtifact.project_id == project.id)
        .order_by(TaskArtifact.task_id, TaskArtifact.artifact_id, TaskArtifact.id).limit(5_000)
    ).all()
    open_draft_total = int(session.scalar(
        select(func.count()).select_from(Proposal).where(
            Proposal.project_id == project.id, Proposal.status == "draft"
        )
    ) or 0)
    open_drafts = service._open_proposal_draft_context(session, project.id, limit=100)
    for draft in open_drafts:
        visible_identities = [
            identity
            for identity in draft.get("source_identities", [])
            if _source_path_visible(
                policy,
                readable_ids,
                str(identity.get("source_root_id") or ""),
                str(identity.get("path") or ""),
            )
        ]
        draft["source_identities"] = visible_identities
        draft["source_identity_count"] = len(visible_identities)
    return {
        "api_version": "1",
        "schema_version": "1",
        "proposal_contract_version": "2",
        "project": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "research_goal": project.research_goal,
            "success_criteria": project.success_criteria,
            "semantic_revision": project.semantic_revision,
            "root_path": project.root_path,
        },
        "intent": {
            "id": intent.id,
            "bound_request_id": intent.proposal_request_id,
            "workflow_mode": intent.workflow_mode,
            "scope_type": intent.scope_type,
            "scope_id": intent.scope_id,
            "allow_completion": intent.allow_completion,
            "user_instructions": intent.instructions,
            "explicit_artifact_locators": [
                _redacted_explicit_locator(session, project.id, item)
                for item in _json_array(intent.artifact_locators_json)
            ],
            "expires_at": jsonable(intent.expires_at),
        },
        "planning_profile": _public_planning_profile(profile),
        "scan_policy": {
            **_public_scan_policy(policy),
            "readable_roots": [
                _public_artifact_root(item) for item in roots if item.id in readable_ids
            ],
            "symlink_following": False,
        },
        "scope": {
            "pipelines": [
                _public_pipeline(item) for item in pipelines if item.id in scope_pipelines
            ],
            "tasks": selected_tasks,
            "edges": [
                _public_edge(item) for item in edges
                if item.source_id in scope_tasks and item.target_id in scope_tasks
            ],
            "derived_sequence_edges": [
                {
                    "source_task_id": item.source_id,
                    "target_task_id": item.target_id,
                    "edge_type": "sequence",
                    "derived": True,
                }
                for item in internal_sequence_edges
            ],
            "ancestor_stubs": [
                {
                    "id": by_id[value].id,
                    "pipeline_id": by_id[value].pipeline_id,
                    "parent_id": by_id[value].parent_id,
                    "title": by_id[value].title,
                    "status": by_id[value].status,
                    "version": by_id[value].entity_version,
                }
                for value in sorted(ancestors) if value in by_id
            ],
            "boundary_task_stubs": [
                {
                    "id": by_id[value].id,
                    "pipeline_id": by_id[value].pipeline_id,
                    "parent_id": by_id[value].parent_id,
                    "title": by_id[value].title,
                    "status": by_id[value].status,
                    "readiness": readiness.get(value, {}).get("readiness"),
                    "version": by_id[value].entity_version,
                }
                for value in sorted(boundary_task_ids) if value in by_id
            ],
            "boundary_edges": [_public_edge(item) for item in boundary_edges],
            "boundary_sequence_edges": [
                {
                    "source_task_id": item.source_id,
                    "target_task_id": item.target_id,
                    "edge_type": "sequence",
                    "derived": True,
                }
                for item in boundary_sequence_edges
            ],
        },
        "source_identity_index": {
            "items": [
                {
                    "id": item.id,
                    "source_root_id": item.source_root_id,
                    "path": item.source_path,
                    "anchor": item.anchor,
                    "opaque_key": item.opaque_key,
                    "fingerprint": item.fingerprint,
                }
                for item in source_rows
            ],
            "total": source_visible_total,
            "limit": 2_000,
            "truncated": source_visible_total > len(source_rows),
        },
        "task_source_identity_index": {
            "items": [
                {
                    "id": item.id,
                    "task_id": item.task_id,
                    "source_reference_id": item.source_reference_id,
                }
                for item in task_source_rows
            ],
            "total": task_source_total,
            "limit": 5_000,
            "truncated": task_source_total > len(task_source_rows),
        },
        "artifact_identity_index": {
            "items": [_redacted_artifact(session, item) for item in artifacts],
            "total": artifact_total,
            "limit": 5_000,
            "truncated": artifact_total > len(artifacts),
        },
        "task_artifact_identity_index": {
            "items": [
                {
                    "id": item.id,
                    "task_id": item.task_id,
                    "artifact_id": item.artifact_id,
                    "role": item.role,
                }
                for item in links
            ],
            "total": link_total,
            "limit": 5_000,
            "truncated": link_total > len(links),
        },
        "journal_identity_index": {
            "items": [
                {
                    "id": item.id,
                    "task_id": item.task_id,
                    "entry_type": item.entry_type,
                    "occurred_at": jsonable(item.occurred_at),
                    "origin_key": item.origin_key,
                    "content_sha256": item.content_sha256,
                    "deleted": item.deleted_at is not None,
                }
                for item in journals
            ],
            "total": journal_total,
            "limit": 5_000,
            "truncated": journal_total > len(journals),
        },
        "open_proposal_drafts": {
            "items": open_drafts,
            "total": open_draft_total,
            "limit": 100,
            "truncated": open_draft_total > len(open_drafts),
        },
        "proposal_contract": {
            "api_version": "1",
            "schema_version": "1",
            "proposal_contract_version": "2",
            "mode": intent.workflow_mode,
            "scope_type": intent.scope_type,
            "scope_id": intent.scope_id,
            "bound_request_id": intent.proposal_request_id,
            "allowed_operation_types": GUIDED_MODE_CONTRACTS[intent.workflow_mode]["operations"],
            "mode_contract": GUIDED_MODE_CONTRACTS[intent.workflow_mode],
            "operation_bases": sorted(GUIDED_OPERATION_BASES),
            "evidence_kinds": {
                key: {"allowed_fields": sorted(value)}
                for key, value in EVIDENCE_KEYS.items()
            },
            "proposal_envelope_json_schema": _v2_contract_schemas()[0],
            "operation_json_schema": _v2_contract_schemas()[1],
        },
    }
