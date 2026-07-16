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
    "notes", "role",
}
OPERATION_UUID_FIELDS = {
    "id", "pipeline_id", "parent_id", "source_task_id", "source_id",
    "target_task_id", "target_id", "task_id", "artifact_id", "artifact_root_id",
}
OPERATION_NUMBER_FIELDS = {"position", "x", "y", "zoom"}
OPERATION_BOOLEAN_FIELDS = {
    "allow_git_metadata", "allow_outside_sources", "cascade", "disabled",
}
OPERATION_INTEGER_FIELDS = {"max_text_file_size", "git_history_limit"}
OPERATION_STRING_LIST_FIELDS = {
    "labels", "preferred_sources", "include_globs", "exclude_globs",
    "sensitive_patterns",
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
    rationale: str = ""
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[dict[str, Any] | str] = Field(default_factory=list)
    source_references: list[dict[str, Any]] = Field(default_factory=list)

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


class ProposalEnvelope(VersionedAPIModel):
    request_id: UUID = Field(default_factory=uuid4)
    project_id: UUID | None = None
    base_semantic_revision: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=800)
    rationale: str = ""
    actor_label: str = "Codex"
    operations: list[Operation] = Field(min_length=1)


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


class BackupRestore(APIModel):
    path: str
    confirm: bool = False
