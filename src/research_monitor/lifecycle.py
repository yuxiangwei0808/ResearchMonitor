from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import delete

from .backup import create_backup
from .database import Database
from .models import IdempotencyRecord, OutboxEvent, Project
from .service import DomainError


def purge_project(database: Database, project_id: str, *, confirm: str) -> dict[str, Any]:
    """Permanently delete a trashed monitor after making a verified backup.

    This never accesses or changes the enrolled research directory.
    The caller must hold the exclusive application lock.
    """
    if confirm != project_id:
        raise DomainError(422, "confirmation_required", "Pass the exact project UUID as confirmation")
    with database.session() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise DomainError(404, "project_not_found", "Project not found")
        if project.trashed_at is None:
            raise DomainError(409, "project_not_trashed", "Only a recoverably trashed project can be purged")
    backup = create_backup(database)
    with database.write_session() as session:
        project = session.get(Project, project_id)
        if project is None or project.trashed_at is None:
            raise DomainError(409, "purge_state_changed", "Project state changed before purge")
        session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.project_id == project_id))
        session.execute(delete(OutboxEvent).where(OutboxEvent.project_id == project_id))
        session.delete(project)
    return {"project_id": project_id, "purged": True, "backup_path": str(backup)}
