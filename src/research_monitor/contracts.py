"""Versioned, machine-readable contracts shared by the API and companion skill."""

from __future__ import annotations

from typing import Any


def _operation(
    entity: str,
    mode: str,
    required: list[str],
    optional: list[str],
    *,
    expected_version: str = "forbidden",
    at_least_one_data_field: bool = False,
) -> dict[str, Any]:
    return {
        "entity": entity,
        "mode": mode,
        "entity_id": "client_generated_required" if mode in {"create", "link"} else "target_required",
        "expected_version": expected_version,
        "data": {
            "required": required,
            "optional": optional,
            "additional_properties": False,
            "at_least_one_field": at_least_one_data_field,
        },
    }


AGENT_OPERATION_SCHEMAS: dict[str, dict[str, Any]] = {
    "pipeline.create": _operation(
        "pipeline", "create", ["title"], ["id", "description", "flow_mode", "position"]
    ),
    "pipeline.update": _operation(
        "pipeline", "update", [], ["title", "description", "flow_mode", "position"],
        expected_version="required", at_least_one_data_field=True,
    ),
    "pipeline.archive": _operation(
        "pipeline", "update", [], [], expected_version="required"
    ),
    "task.create": _operation(
        "task", "create", ["pipeline_id", "title"],
        [
            "id", "parent_id", "user_key", "description", "kind", "status", "outcome",
            "priority", "labels", "target_date", "position", "completion_criteria",
            "blocker_reason", "completion_summary", "completion_source",
            "completion_override_reason", "child_flow_mode",
        ],
    ),
    "task.update": _operation(
        "task", "update", [],
        [
            "pipeline_id", "parent_id", "user_key", "title", "description", "kind", "status",
            "outcome", "priority", "labels", "target_date", "position",
            "completion_criteria", "blocker_reason", "completion_summary", "completion_source",
            "completion_override_reason", "child_flow_mode",
        ],
        expected_version="required", at_least_one_data_field=True,
    ),
    "task.move": _operation(
        "task", "update", [], ["pipeline_id", "parent_id", "position"],
        expected_version="required", at_least_one_data_field=True,
    ),
    "edge.create": _operation(
        "edge", "create", ["source_task_id", "target_task_id"],
        ["id", "edge_type", "disabled", "waiver_reason"],
    ),
    "edge.update": _operation(
        "edge", "update", [], ["edge_type", "disabled", "waiver_reason"],
        expected_version="required", at_least_one_data_field=True,
    ),
    "journal.create": _operation(
        "journal", "create", ["task_id", "content"], ["id", "entry_type", "occurred_at"]
    ),
    "journal.update": _operation(
        "journal", "update", [], ["content", "entry_type", "occurred_at"],
        expected_version="required", at_least_one_data_field=True,
    ),
    "artifact.create": _operation(
        "artifact", "create", ["locator"],
        ["id", "kind", "artifact_root_id", "provider", "label", "notes"],
    ),
    "artifact.update": _operation(
        "artifact", "update", [],
        ["kind", "locator", "artifact_root_id", "provider", "label", "notes"],
        expected_version="required", at_least_one_data_field=True,
    ),
    "task_artifact.link": _operation(
        "task_artifact", "link", ["task_id", "artifact_id"], ["id", "role", "notes"]
    ),
}

AGENT_OPERATION_TYPES = frozenset(AGENT_OPERATION_SCHEMAS)

STABLE_CLI_COMMANDS = [
    "research-monitor version --json",
    "research-monitor open [--no-open] [--json]",
    "research-monitor project list --json",
    "research-monitor project resolve --path PATH --json",
    "research-monitor agent context --project UUID --json",
    "research-monitor proposal validate --project UUID --file FILE_OR_-",
    "research-monitor proposal create --project UUID --file FILE_OR_-",
    "research-monitor proposal inspect PROPOSAL_ID --json",
    "research-monitor export project --project UUID [--output PATH]",
    "research-monitor backup create [--output PATH] [--force]",
    "research-monitor backup restore PATH --confirm",
    "research-monitor skill status",
    "research-monitor skill install [--force]",
    "research-monitor skill update [--force]",
]


def render_cli_reference_block() -> str:
    return "```text\n" + "\n".join(STABLE_CLI_COMMANDS) + "\n```"


def render_operation_reference_block() -> str:
    lines = [
        "| Agent operation | `entity_id` | `expected_version` | Required `data` | Optional `data` |",
        "|---|---|---|---|---|",
    ]
    for name, contract in AGENT_OPERATION_SCHEMAS.items():
        required = ", ".join(f"`{value}`" for value in contract["data"]["required"]) or "—"
        optional = ", ".join(f"`{value}`" for value in contract["data"]["optional"]) or "—"
        lines.append(
            f"| `{name}` | {contract['entity_id']} | {contract['expected_version']} | "
            f"{required} | {optional} |"
        )
    return "\n".join(lines)
