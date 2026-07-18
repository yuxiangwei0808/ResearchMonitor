from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import inspect

from .models import Project


JSON_COLUMNS = {
    "preferred_sources_json": "preferred_sources",
    "include_globs_json": "include_globs",
    "exclude_globs_json": "exclude_globs",
    "sensitive_patterns_json": "sensitive_patterns",
    "readable_source_root_ids_json": "readable_source_root_ids",
    "preferred_pipeline_names_json": "preferred_pipeline_names",
    "protected_pipeline_ids_json": "protected_pipeline_ids",
    "protected_task_ids_json": "protected_task_ids",
    "artifact_locators_json": "artifact_locators",
    "scan_summary_json": "scan_summary",
    "top_level_evidence_json": "top_level_evidence",
    "top_level_source_references_json": "top_level_source_references",
    "labels_json": "labels",
    "prerequisites_json": "prerequisite_operation_ids",
    "evidence_json": "evidence",
    "source_references_json": "source_references",
    "before_json": "before",
    "after_json": "after",
    "payload_json": "payload",
    "operation_json": "operation",
}


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return value


def model_dict(model: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = exclude or set()
    result: dict[str, Any] = {}
    for column in inspect(model).mapper.column_attrs:
        name = column.key
        if name in exclude:
            continue
        value = getattr(model, name)
        if name in JSON_COLUMNS:
            output_name = JSON_COLUMNS[name]
            try:
                result[output_name] = json.loads(value or "null")
            except (TypeError, ValueError):
                result[output_name] = None
        else:
            result[name] = jsonable(value)
    return result


def project_dict(project: Project) -> dict[str, Any]:
    result = model_dict(project)
    root = Path(project.root_path)
    try:
        available = root.is_dir() and root.resolve(strict=True) == root
    except (FileNotFoundError, OSError, RuntimeError):
        available = False
    result["availability"] = "available" if available else "unavailable"
    return result


def canonical_json(value: Any) -> str:
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


IDEMPOTENCY_WRAPPER_KEY = "_research_monitor_idempotency_v1"


def request_fingerprint(value: Any) -> str:
    """Return a stable hash for the complete logical idempotent request."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def pack_idempotent_response(response: dict[str, Any], fingerprint: str) -> str:
    return canonical_json(
        {
            IDEMPOTENCY_WRAPPER_KEY: {
                "fingerprint": fingerprint,
                "response": response,
            }
        }
    )


def unpack_idempotent_response(value: str) -> tuple[dict[str, Any], str | None]:
    decoded = json.loads(value)
    wrapper = decoded.get(IDEMPOTENCY_WRAPPER_KEY) if isinstance(decoded, dict) else None
    if isinstance(wrapper, dict) and isinstance(wrapper.get("response"), dict):
        fingerprint = wrapper.get("fingerprint")
        return wrapper["response"], str(fingerprint) if fingerprint else None
    # Records from pre-fingerprint releases remain readable. Their exact input
    # cannot be reconstructed, so collision enforcement applies to new records.
    return decoded, None
