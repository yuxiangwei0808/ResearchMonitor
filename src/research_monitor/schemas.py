from __future__ import annotations

import math
from datetime import date
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import API_VERSION, SCHEMA_VERSION


ActorType = Literal["ui", "agent", "import", "system"]

OPERATION_STRING_FIELDS = {
    "name", "title", "description", "research_goal", "success_criteria", "color",
    "flow_mode", "user_key", "kind", "status", "outcome", "priority",
    "target_date", "completion_criteria", "blocker_reason", "completion_summary",
    "completion_actor", "completion_source", "completion_override_reason",
    "child_flow_mode", "edge_type", "waiver_reason", "entry_type", "content",
    "occurred_at", "canonical_path", "root_path", "locator", "provider", "label",
    "notes", "role", "task_granularity", "planning_horizon", "inference_policy",
    "terminology_notes", "additional_instructions",
}
OPERATION_UUID_FIELDS = {
    "id", "pipeline_id", "parent_id", "source_task_id", "source_id",
    "target_task_id", "target_id", "task_id", "artifact_id", "artifact_root_id",
}
OPERATION_NUMBER_FIELDS = {"position", "x", "y", "zoom"}
OPERATION_BOOLEAN_FIELDS = {
    "allow_git_metadata", "allow_outside_sources", "cascade", "disabled",
    "allow_completion",
}
OPERATION_INTEGER_FIELDS = {
    "max_text_file_size", "git_history_limit", "max_files_per_scan",
    "max_total_text_bytes", "max_nesting_depth", "max_new_tasks_per_proposal",
}
OPERATION_STRING_LIST_FIELDS = {
    "labels", "preferred_sources", "include_globs", "exclude_globs",
    "sensitive_patterns", "readable_source_root_ids", "preferred_pipeline_names",
    "protected_pipeline_ids", "protected_task_ids",
}


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VersionedAPIModel(APIModel):
    api_version: str = API_VERSION
    schema_version: str = SCHEMA_VERSION

    @field_validator("api_version")
    @classmethod
    def check_api_version(cls, value: str) -> str:
        if value != API_VERSION:
            raise ValueError(f"unsupported API version: {value}")
        return value

    @field_validator("schema_version")
    @classmethod
    def check_schema_version(cls, value: str) -> str:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {value}")
        return value


class ProjectCreate(APIModel):
    name: str = Field(min_length=1, max_length=240)
    root_path: str = Field(min_length=1)
    description: str = ""
    research_goal: str = ""
    success_criteria: str = ""
    color: str = "#4f46e5"


class Operation(APIModel):
    id: UUID = Field(default_factory=uuid4)
    type: str = Field(min_length=1, max_length=80)
    entity_id: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)
    data: dict[str, Any] = Field(default_factory=dict)
    atomic_group_id: UUID | None = None
    prerequisite_operation_ids: list[UUID] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=4096)
    confidence: float | None = Field(default=None, ge=0, le=1)
    basis: Literal["source_evidence", "user_instruction", "inference"] | None = None
    evidence: list[dict[str, Any] | str] = Field(default_factory=list, max_length=8)
    source_references: list[dict[str, Any]] = Field(default_factory=list, max_length=8)

    @field_validator("data")
    @classmethod
    def check_data_shapes(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Reject malformed nested values before they reach domain conversions.

        Operation data deliberately remains extensible, but values for the stable
        fields have stable JSON shapes.  Keeping this check at the request-schema
        boundary prevents user-controlled values from surfacing as Python or
        SQLAlchemy conversion exceptions while leaving unrelated server errors
        visible.
        """
        for field in OPERATION_STRING_FIELDS:
            raw = value.get(field)
            if field in value and raw is not None and not isinstance(raw, str):
                raise ValueError(f"operation data field {field!r} must be a string or null")
        for field in OPERATION_UUID_FIELDS:
            raw = value.get(field)
            if field not in value or raw is None or raw == "":
                continue
            if not isinstance(raw, str):
                raise ValueError(f"operation data field {field!r} must be a UUID string or null")
            try:
                UUID(raw)
            except ValueError as exc:
                raise ValueError(f"operation data field {field!r} must be a valid UUID") from exc
        for field in OPERATION_NUMBER_FIELDS:
            raw = value.get(field)
            if field not in value:
                continue
            finite = False
            if not isinstance(raw, bool) and isinstance(raw, (int, float)):
                try:
                    finite = math.isfinite(float(raw))
                except (OverflowError, ValueError):
                    finite = False
            if not finite:
                raise ValueError(f"operation data field {field!r} must be a finite number")
        for field in OPERATION_BOOLEAN_FIELDS:
            if field in value and not isinstance(value[field], bool):
                raise ValueError(f"operation data field {field!r} must be a boolean")
        for field in OPERATION_INTEGER_FIELDS:
            raw = value.get(field)
            if field in value and (isinstance(raw, bool) or not isinstance(raw, int)):
                raise ValueError(f"operation data field {field!r} must be an integer")
        for field in OPERATION_STRING_LIST_FIELDS:
            raw = value.get(field)
            if field in value and (
                not isinstance(raw, list) or any(not isinstance(item, str) for item in raw)
            ):
                raise ValueError(f"operation data field {field!r} must be a list of strings")
        target_date = value.get("target_date")
        if isinstance(target_date, str) and target_date:
            try:
                date.fromisoformat(target_date)
            except ValueError as exc:
                raise ValueError("operation data field 'target_date' must be an ISO date") from exc
        return value

    @model_validator(mode="after")
    def validate_entity_identity(self) -> "Operation":
        """Keep the two supported client-ID encodings unambiguous."""
        raw_data_id = self.data.get("id")
        if raw_data_id is None:
            return self
        data_id = UUID(str(raw_data_id))
        if self.entity_id is not None and self.entity_id != data_id:
            raise ValueError("operation entity_id and data.id must match")
        return self

    def resolved_entity_id(self) -> UUID | None:
        raw_data_id = self.data.get("id")
        return UUID(str(raw_data_id)) if raw_data_id is not None else self.entity_id


class MutationEnvelope(VersionedAPIModel):
    request_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    base_semantic_revision: int = Field(ge=0)
    actor_type: ActorType = "ui"
    actor_label: str = ""
    operations: list[Operation] = Field(min_length=1)


class MutationUndo(APIModel):
    request_id: UUID = Field(default_factory=uuid4)
    base_semantic_revision: int = Field(ge=0)

class LayoutMutationEnvelope(VersionedAPIModel):
    request_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    base_layout_revision: int = Field(ge=0)
    actor_type: ActorType = "ui"
    actor_label: str = ""
    operations: list[Operation] = Field(min_length=1)


class ScanSummary(APIModel):
    files_considered: int = Field(default=0, ge=0)
    files_read: int = Field(default=0, ge=0)
    text_bytes_read: int = Field(default=0, ge=0)
    truncated: bool = False
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("limitations", mode="before")
    @classmethod
    def accept_legacy_limitation_string(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [value] if value else []
        return value

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 500 for item in value):
            raise ValueError(
                "scan-summary limitations must be nonempty and at most 500 characters"
            )
        return value


class ProposalEnvelope(VersionedAPIModel):
    request_id: UUID = Field(default_factory=uuid4)
    project_id: UUID | None = None
    base_semantic_revision: int = Field(ge=0)
    proposal_contract_version: Literal["1", "2"] = "1"
    intent_id: UUID | None = None
    result_kind: Literal["changes", "no_changes"] = "changes"
    no_change_reason: Literal["up_to_date", "insufficient_evidence", "ambiguous_sources"] | None = None
    summary: str = Field(min_length=1, max_length=800)
    rationale: str = Field(default="", max_length=8192)
    actor_label: str = "Codex"
    scan_summary: ScanSummary = Field(default_factory=ScanSummary)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    source_references: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    operations: list[Operation] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_result_shape(self) -> "ProposalEnvelope":
        if self.proposal_contract_version == "1":
            if self.intent_id is not None:
                raise ValueError("v1 proposals cannot carry an intent_id")
            if self.result_kind != "changes" or self.no_change_reason is not None:
                raise ValueError("v1 proposals support change results only")
            if not self.operations:
                raise ValueError("v1 proposals require at least one operation")
            return self
        if self.intent_id is None:
            raise ValueError("v2 proposals require intent_id")
        missing_wire_fields = {"result_kind", "scan_summary"} - self.model_fields_set
        if missing_wire_fields:
            raise ValueError(
                f"v2 proposals require explicit {', '.join(sorted(missing_wire_fields))}"
            )
        if self.result_kind == "changes":
            if not self.operations:
                raise ValueError("change results require at least one operation")
            if self.no_change_reason is not None:
                raise ValueError("change results cannot carry no_change_reason")
        else:
            if self.operations:
                raise ValueError("no-change results cannot contain operations")
            if self.no_change_reason is None:
                raise ValueError("no-change results require no_change_reason")
            if not self.evidence and not self.source_references:
                raise ValueError("no-change results require evidence or source references")
        return self


class ProposalApply(APIModel):
    request_id: UUID = Field(default_factory=uuid4)
    selected_operation_ids: list[UUID] = Field(min_length=1)
    operation_overrides: list[Operation] = Field(default_factory=list)


class ProposalReject(APIModel):
    request_id: UUID = Field(default_factory=uuid4)
    reason: str = ""


class ProposalRevision(VersionedAPIModel):
    """A complete human-edited replacement for one open proposal draft."""

    request_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    base_semantic_revision: int = Field(ge=0)
    actor_type: Literal["ui"] = "ui"
    actor_label: str = Field(default="Research Monitor UI", min_length=1, max_length=240)
    summary: str = Field(min_length=1, max_length=800)
    rationale: str = ""
    operations: list[Operation] = Field(min_length=1)



class ExplicitArtifactLocator(APIModel):
    kind: Literal["local", "url"]
    locator: str = Field(min_length=1, max_length=4096)
    artifact_root_id: UUID | None = None
    provider: str = Field(default="", max_length=120)
    label: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_locator_root(self) -> "ExplicitArtifactLocator":
        if self.kind == "local" and self.artifact_root_id is None:
            raise ValueError("local artifact locators require artifact_root_id")
        if self.kind == "url" and self.artifact_root_id is not None:
            raise ValueError("URL artifact locators cannot carry artifact_root_id")
        return self


class AgentPromptCreate(VersionedAPIModel):
    mode: Literal[
        "initialize_structure", "expand_task", "reconcile_progress",
        "suggest_next_work", "record_update", "link_artifacts",
    ]
    scope_type: Literal["project", "pipeline", "task"]
    scope_id: UUID | None = None
    instructions: str = Field(default="", max_length=8192)
    force_fresh: bool = False
    allow_completion: bool = False
    artifact_locators: list[ExplicitArtifactLocator] = Field(default_factory=list, max_length=50)
    regenerate_proposal_id: UUID | None = None

    @model_validator(mode="after")
    def validate_scope_and_mode(self) -> "AgentPromptCreate":
        if self.scope_type == "project" and self.scope_id is not None:
            raise ValueError("project scope cannot carry scope_id")
        if self.scope_type != "project" and self.scope_id is None:
            raise ValueError("pipeline and task scopes require scope_id")
        allowed_scopes = {
            "initialize_structure": {"project"},
            "expand_task": {"task"},
            "reconcile_progress": {"project", "pipeline", "task"},
            "suggest_next_work": {"project", "pipeline"},
            "record_update": {"task"},
            "link_artifacts": {"task"},
        }
        if self.scope_type not in allowed_scopes[self.mode]:
            raise ValueError(f"{self.mode} does not support {self.scope_type} scope")
        if len(self.instructions.encode("utf-8")) > 8192:
            raise ValueError("instructions must not exceed 8 KiB encoded as UTF-8")
        if self.mode == "record_update" and not self.instructions.strip():
            raise ValueError("record_update requires instructions")
        if self.mode == "link_artifacts" and not self.artifact_locators:
            raise ValueError("link_artifacts requires at least one artifact locator")
        if self.allow_completion and self.mode != "record_update":
            raise ValueError("allow_completion is valid only for record_update")
        return self

class BackupRestore(APIModel):
    path: str
    confirm: bool = False
