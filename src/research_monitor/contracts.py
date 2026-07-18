"""Versioned, machine-readable contracts shared by the API and companion skill."""

from __future__ import annotations

from typing import Any


CAPABILITIES = {
    "guided_agent_intents": 1,
    "proposal_contract": 2,
    "scoped_agent_context": 1,
    "no_change_results": 1,
}


GUIDED_MODE_CONTRACTS: dict[str, dict[str, Any]] = {
    "initialize_structure": {
        "scopes": ["project"],
        "operations": ["pipeline.create", "task.create", "edge.create"],
        "required_operation": None,
    },
    "expand_task": {
        "scopes": ["task"],
        "operations": ["task.create", "task.update", "edge.create"],
        "required_operation": None,
        "task_update_fields": [
            "description", "priority", "labels", "target_date",
            "completion_criteria", "child_flow_mode",
        ],
    },
    "reconcile_progress": {
        "scopes": ["project", "pipeline", "task"],
        "operations": [
            "task.update", "journal.create", "artifact.create",
            "task_artifact.link",
        ],
        "required_operation": None,
        "task_update_fields": [
            "status", "outcome", "blocker_reason", "completion_summary",
            "completion_source",
        ],
    },
    "suggest_next_work": {
        "scopes": ["project", "pipeline"],
        "operations": ["pipeline.create", "task.create", "edge.create"],
        "required_operation": None,
    },
    "record_update": {
        "scopes": ["task"],
        "operations": [
            "task.update", "journal.create", "artifact.create",
            "task_artifact.link",
        ],
        "required_operation": "journal.create",
        "task_update_fields": [
            "status", "outcome", "blocker_reason", "completion_summary",
            "completion_source",
        ],
    },
    "link_artifacts": {
        "scopes": ["task"],
        "operations": ["artifact.create", "task_artifact.link"],
        "required_operation": None,
    },
}

GUIDED_EVIDENCE_KINDS = frozenset(
    {
        "source_text",
        "git_metadata",
        "completion_text",
        "result_evidence",
        "existing_artifact",
        "user_instruction",
        "inference",
    }
)
GUIDED_OPERATION_BASES = frozenset(
    {"source_evidence", "user_instruction", "inference"}
)
GUIDED_RESULT_KINDS = frozenset({"changes", "no_changes"})
GUIDED_NO_CHANGE_REASONS = frozenset(
    {"up_to_date", "insufficient_evidence", "ambiguous_sources"}
)

# Single source of truth for guided evidence validation, live JSON Schema, and
# the bundled companion reference.
SOURCE_REFERENCE_KEYS = frozenset({
    "monitor_reference_id", "id", "source_root_id", "path", "source_path",
    "anchor", "summary", "content_hash", "fingerprint", "opaque_key",
})

GUIDED_EVIDENCE_FIELDS: dict[str, frozenset[str]] = {
    "source_text": frozenset({
        "kind", "source_root_id", "path", "anchor", "summary", "content_hash",
    }),
    "git_metadata": frozenset({
        "kind", "summary", "commit", "path", "content_hash",
    }),
    "completion_text": frozenset({
        "kind", "summary", "source_reference_id", "source_root_id", "path",
        "anchor", "content_hash", "monitor_reference_id", "opaque_key",
    }),
    "result_evidence": frozenset({
        "kind", "summary", "artifact_id", "source_reference_id",
        "source_root_id", "path", "anchor", "content_hash",
        "monitor_reference_id", "opaque_key",
    }),
    "existing_artifact": frozenset({"kind", "summary", "artifact_id"}),
    "user_instruction": frozenset({"kind", "summary", "intent_id"}),
    "inference": frozenset({"kind", "summary", "supporting_identities"}),
}

GUIDED_EVIDENCE_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "source_text": frozenset({
        "kind", "source_root_id", "path", "anchor", "summary", "content_hash",
    }),
    "git_metadata": frozenset({"kind", "summary"}),
    "completion_text": frozenset({"kind", "summary"}),
    "result_evidence": frozenset({"kind", "summary"}),
    "existing_artifact": frozenset({"kind", "summary", "artifact_id"}),
    "user_instruction": frozenset({"kind", "summary", "intent_id"}),
    "inference": frozenset({"kind", "summary", "supporting_identities"}),
}

GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES: dict[
    str, tuple[frozenset[str], ...]
] = {
    "git_metadata": (
        frozenset({"commit"}),
        frozenset({"content_hash"}),
    ),
    "completion_text": (
        frozenset({"source_reference_id", "content_hash"}),
        frozenset({"source_root_id", "path", "anchor", "content_hash"}),
    ),
    "result_evidence": (
        frozenset({"source_reference_id", "content_hash"}),
        frozenset({"source_root_id", "path", "anchor", "content_hash"}),
    ),
}


def render_guided_proposal_reference_block() -> str:
    lines = [
        "| Guided mode | Allowed scope | Allowed operations | Allowed `task.update` data | Required result operation |",
        "|---|---|---|---|---|",
    ]
    for mode, contract in GUIDED_MODE_CONTRACTS.items():
        scopes = ", ".join(f"`{value}`" for value in contract["scopes"])
        operations = ", ".join(
            f"`{value}`" for value in contract["operations"]
        )
        update_fields = ", ".join(
            f"`{value}`" for value in contract.get("task_update_fields", [])
        ) or "—"
        required = (
            f"`{contract['required_operation']}`"
            if contract["required_operation"]
            else "—"
        )
        lines.append(
            f"| `{mode}` | {scopes} | {operations} | {update_fields} | {required} |"
        )
    lines.extend(
        [
            "",
            "Operation bases: "
            + ", ".join(f"`{value}`" for value in sorted(GUIDED_OPERATION_BASES))
            + ".",
            "Evidence kinds: "
            + ", ".join(f"`{value}`" for value in sorted(GUIDED_EVIDENCE_KINDS))
            + ".",
            "Result kinds: "
            + ", ".join(f"`{value}`" for value in sorted(GUIDED_RESULT_KINDS))
            + ".",
            "No-change reasons: "
            + ", ".join(f"`{value}`" for value in sorted(GUIDED_NO_CHANGE_REASONS))
            + ".",
        ]
    )
    return "\n".join(lines)


def render_evidence_reference_block() -> str:
    """Render exact guided evidence fields from validator-owned definitions."""

    lines = [
        "| Evidence kind | Always required | Identity requirement | Optional fields |",
        "|---|---|---|---|",
    ]
    for kind, fields in GUIDED_EVIDENCE_FIELDS.items():
        required = GUIDED_EVIDENCE_REQUIRED_FIELDS[kind]
        alternatives = GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES.get(kind, ())
        identity_fields = set().union(*alternatives) if alternatives else set()
        optional = set(fields) - set(required) - identity_fields
        required_text = ", ".join(
            f"`{field}`" for field in sorted(required - {"kind"})
        ) or "—"
        identity_text = " or ".join(
            " + ".join(f"`{field}`" for field in sorted(alternative))
            for alternative in alternatives
        ) or "—"
        optional_text = ", ".join(
            f"`{field}`" for field in sorted(optional)
        ) or "—"
        lines.append(
            f"| `{kind}` | {required_text} | {identity_text} | {optional_text} |"
        )
    return "\n".join(lines)


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
    "research-monitor agent context --project UUID --intent UUID --json",
    "research-monitor proposal validate --project UUID --file FILE_OR_-",
    "research-monitor proposal create --project UUID --file FILE_OR_-",
    "research-monitor proposal inspect PROPOSAL_ID --json",
    "research-monitor export project --project UUID [--output PATH]",
    "research-monitor backup create [--output PATH] [--force]",
    "research-monitor backup restore PATH --confirm [--rollback-to-v0.1]",
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
