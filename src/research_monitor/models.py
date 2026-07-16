from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(240))
    root_path: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    research_goal: Mapped[str] = mapped_column(Text, default="")
    success_criteria: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(32), default="#4f46e5")
    semantic_revision: Mapped[int] = mapped_column(Integer, default=0)
    layout_revision: Mapped[int] = mapped_column(Integer, default=0)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)
    archived_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    trashed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_manual_update_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_proposal_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    last_agent_sync_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ScanPolicy(Base):
    __tablename__ = "scan_policies"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    preferred_sources_json: Mapped[str] = mapped_column(Text, default="[]")
    include_globs_json: Mapped[str] = mapped_column(Text, default='["**/*.md","**/*.txt","**/*.py"]')
    exclude_globs_json: Mapped[str] = mapped_column(Text, default="[]")
    sensitive_patterns_json: Mapped[str] = mapped_column(Text, default="[]")
    max_text_bytes: Mapped[int] = mapped_column(Integer, default=2 * 1024 * 1024)
    allow_git_metadata: Mapped[bool] = mapped_column(Boolean, default=True)
    git_history_limit: Mapped[int] = mapped_column(Integer, default=100)
    allow_outside_sources: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_symlinks: Mapped[bool] = mapped_column(Boolean, default=False)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)


class ArtifactRoot(Base):
    __tablename__ = "artifact_roots"
    __table_args__ = (UniqueConstraint("project_id", "root_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(120))
    root_path: Mapped[str] = mapped_column(Text)
    is_project_root: Mapped[bool] = mapped_column(Boolean, default=False)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Pipeline(Base):
    __tablename__ = "pipelines"
    __table_args__ = (Index("ix_pipeline_project_order", "project_id", "order_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    flow_mode: Mapped[str] = mapped_column(String(20), default="sequential")
    order_index: Mapped[float] = mapped_column(Float, default=0)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)
    archived_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    deletion_batch_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_task_project_pipeline_parent_order", "project_id", "pipeline_id", "parent_id", "order_index"),
        UniqueConstraint("project_id", "user_key", name="uq_task_project_user_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"))
    parent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    user_key: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="task")
    title: Mapped[str] = mapped_column(String(800))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="planned")
    outcome: Mapped[str] = mapped_column(String(30), default="not_applicable")
    priority: Mapped[str] = mapped_column(String(30), default="recommended")
    labels_json: Mapped[str] = mapped_column(Text, default="[]")
    target_date: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    order_index: Mapped[float] = mapped_column(Float, default=0)
    completion_criteria: Mapped[str] = mapped_column(Text, default="")
    blocker_reason: Mapped[str] = mapped_column(Text, default="")
    completion_summary: Mapped[str] = mapped_column(Text, default="")
    completion_actor: Mapped[str] = mapped_column(String(240), default="")
    completion_source: Mapped[str] = mapped_column(String(240), default="")
    completion_override_reason: Mapped[str] = mapped_column(Text, default="")
    completion_provenance: Mapped[str] = mapped_column(String(30), default="")
    child_flow_mode: Mapped[str] = mapped_column(String(20), default="freeform")
    entity_version: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    deletion_batch_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TaskEdge(Base):
    __tablename__ = "task_edges"
    __table_args__ = (UniqueConstraint("project_id", "source_id", "target_id", "edge_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    target_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    edge_type: Mapped[str] = mapped_column(String(20))
    waived_reason: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    disabled_reason: Mapped[str] = mapped_column(Text, default="")
    disabled_batch_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class JournalEntry(Base):
    __tablename__ = "journal_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    entry_type: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("project_id", "root_id", "locator", name="uq_artifact_locator"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    root_id: Mapped[Optional[str]] = mapped_column(ForeignKey("artifact_roots.id", ondelete="RESTRICT"), nullable=True)
    locator_type: Mapped[str] = mapped_column(String(20))
    locator: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(120), default="local")
    label: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    validation_warning: Mapped[str] = mapped_column(Text, default="")
    entity_version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TaskArtifact(Base):
    __tablename__ = "task_artifacts"
    __table_args__ = (UniqueConstraint("task_id", "artifact_id", "role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TaskLayout(Base):
    __tablename__ = "task_layouts"
    __table_args__ = (UniqueConstraint("project_id", "task_id", "scope_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    scope_id: Mapped[str] = mapped_column(String(36), default="root")
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)


class GraphViewport(Base):
    __tablename__ = "graph_viewports"
    __table_args__ = (UniqueConstraint("project_id", "scope_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    scope_id: Mapped[str] = mapped_column(String(36), default="root")
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    zoom: Mapped[float] = mapped_column(Float, default=1)
    entity_version: Mapped[int] = mapped_column(Integer, default=1)


class SourceReference(Base):
    __tablename__ = "source_references"
    __table_args__ = (
        UniqueConstraint("project_id", "source_path", "anchor", "opaque_key", name="uq_source_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    source_path: Mapped[str] = mapped_column(Text)
    anchor: Mapped[str] = mapped_column(Text, default="")
    opaque_key: Mapped[str] = mapped_column(String(240), default="")
    fingerprint: Mapped[str] = mapped_column(String(128), default="")
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    request_id: Mapped[str] = mapped_column(String(36), unique=True)
    base_semantic_revision: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(800))
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    actor_label: Mapped[str] = mapped_column(String(240), default="Codex")
    rejection_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class ProposalOperation(Base):
    __tablename__ = "proposal_operations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id", ondelete="CASCADE"))
    operation_type: Mapped[str] = mapped_column(String(80))
    operation_json: Mapped[str] = mapped_column(Text)
    atomic_group_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    prerequisites_json: Mapped[str] = mapped_column(Text, default="[]")
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    source_references_json: Mapped[str] = mapped_column(Text, default="[]")
    disposition: Mapped[str] = mapped_column(String(20), default="pending")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer, index=True)
    actor_type: Mapped[str] = mapped_column(String(30))
    actor_label: Mapped[str] = mapped_column(String(240), default="")
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(36), default="")
    before_json: Mapped[str] = mapped_column(Text, default="null")
    after_json: Mapped[str] = mapped_column(Text, default="null")
    request_id: Mapped[str] = mapped_column(String(36), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(80))
    response_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SchemaVersion(Base):
    __tablename__ = "schema_versions"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(default=utcnow)
