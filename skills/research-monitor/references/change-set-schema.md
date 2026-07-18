# Review-only proposal schema

Use this reference to shape a result, then replace every stale example detail with the live `proposal_contract` returned by intent-bound context. The CLI injects or verifies the project selected by `--project`.

## Guided v2 changes envelope

```json
{
  "api_version": "1",
  "schema_version": "1",
  "proposal_contract_version": "2",
  "request_id": "3cf16d86-df0f-44af-9c8f-6bf513f35719",
  "project_id": "b9582970-d1ee-45a5-b606-af185f2030a5",
  "intent_id": "e17cbb96-d255-48c6-a58f-066f0fe56745",
  "base_semantic_revision": 12,
  "result_kind": "changes",
  "summary": "Reconcile documented preprocessing progress",
  "rationale": "The preferred tracker explicitly records two state changes.",
  "actor_label": "Codex research-monitor skill",
  "scan_summary": {
    "files_considered": 8,
    "files_read": 3,
    "text_bytes_read": 18420,
    "truncated": false,
    "limitations": []
  },
  "evidence": [],
  "source_references": [],
  "operations": []
}
```

Requirements:

- Copy API/schema versions, intent ID, bound `request_id`, project ID, and base semantic revision from live intent context.
- Set `proposal_contract_version` to `"2"` for an intent-bound result.
- Never supply a different workflow mode or scope; the server derives both from the intent.
- Reuse the bound request UUID only for this first payload or an exact transport retry. Never change content under that UUID.
- Keep `summary` factual and concise. Put conflicts, duplicate candidates, truncation, and scan limitations in `rationale` and the structured scan summary.
- Use `result_kind: "changes"` only with at least one operation.

`scan_summary` has exactly five fields:

- `files_considered`, `files_read`, and `text_bytes_read`: nonnegative integers. The last two must not exceed the intent-bound scan policy.
- `truncated`: a boolean that is true whenever a file, byte, output, or identity limit stopped complete inspection.
- `limitations`: an array of at most twenty nonempty strings, each at most 500 characters. Use an empty array only when there was no material scan limitation.

Count actual files and exact file bytes read. Do not count a file merely because its identity was already present in monitor context. The object accepts no additional fields.


## Guided v2 no-change envelope

```json
{
  "api_version": "1",
  "schema_version": "1",
  "proposal_contract_version": "2",
  "request_id": "3cf16d86-df0f-44af-9c8f-6bf513f35719",
  "project_id": "b9582970-d1ee-45a5-b606-af185f2030a5",
  "intent_id": "e17cbb96-d255-48c6-a58f-066f0fe56745",
  "base_semantic_revision": 12,
  "result_kind": "no_changes",
  "no_change_reason": "up_to_date",
  "summary": "The bounded source scan agrees with the monitor.",
  "rationale": "No evidence-backed semantic difference was found.",
  "actor_label": "Codex research-monitor skill",
  "scan_summary": {
    "files_considered": 8,
    "files_read": 3,
    "text_bytes_read": 18420,
    "truncated": false,
    "limitations": []
  },
  "evidence": [],
  "source_references": [],
  "operations": []
}
```

A no-change result has zero operations and exactly one reason:

- `up_to_date`: the completed bounded scan supports the current monitor state.
- `insufficient_evidence`: permitted sources cannot support a safe change.
- `ambiguous_sources`: conflicting identities or sources prevent a safe change.

Include the bounded scan summary and top-level structured evidence/source references required by the live contract. A no-change result is stored closed, cannot be applied, and does not change semantic revision.

## Guided operation

```json
{
  "id": "83433ec4-c31b-4bd5-a77e-9676b1db09d4",
  "type": "task.create",
  "entity_id": "50d48c32-7409-498b-b63c-bb148f16f189",
  "expected_version": null,
  "atomic_group_id": "c9d2d68f-b45a-44be-abbb-41825402566f",
  "prerequisite_operation_ids": [
    "97874039-0e1e-470c-8404-458a84369986"
  ],
  "basis": "source_evidence",
  "data": {},
  "rationale": "The preferred experiment plan explicitly defines this required step.",
  "confidence": 0.93,
  "evidence": [],
  "source_references": []
}
```

Requirements:

- Give every operation a stable UUID. Give created entities client UUIDs so later operations can reference them.
- Give every guided operation exactly one basis: `source_evidence`, `user_instruction`, or `inference`.
- Use `expected_version` for an existing semantic entity. Use null only for creates when the live contract permits it.
- Declare prerequisites explicitly. Put inseparable operations in one atomic group; a new artifact and its required in-scope task link must share one.
- Send only changed fields. Never send `completion_override_reason` from an agent.
- Use confidence from 0 through 1 for source-to-operation support, not scientific success. Cap inference at 0.79.
- Include evidence/source references required by the operation basis and live mode contract.
- Expect inferred, completion, dropping, waiver, archival, structural, shared-entity, and all legacy operations to start unselected for explicit review.

## Structured evidence

Use exact live shapes. The bundled baseline source form is:

```json
{
  "basis": "source_evidence",
  "evidence": [
    {
      "kind": "source_text",
      "source_root_id": "a33df30b-9fb6-426a-a84b-6a0c4ab854eb",
      "path": "docs/TRACKER.md",
      "anchor": "preprocessing",
      "summary": "The tracker marks preprocessing complete and names its output.",
      "content_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "source_references": [
    {
      "source_root_id": "a33df30b-9fb6-426a-a84b-6a0c4ab854eb",
      "path": "docs/TRACKER.md",
      "anchor": "preprocessing",
      "opaque_key": "DATA-02",
      "fingerprint": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```


Every `source_text.content_hash` and source-reference `fingerprint`/`content_hash` is the lowercase 64-hex SHA-256 of the exact complete file bytes. Do not include a `sha256:` prefix. The bounded anchor identifies the supported passage but is never the hash input.

Each evidence object requires `kind` and a nonempty `summary` of at most 1,000 characters. The exact field table is generated from the same definitions used by live validation and JSON Schema:

<!-- BEGIN GENERATED: guided-evidence-fields -->
| Evidence kind | Always required | Identity requirement | Optional fields |
|---|---|---|---|
| `source_text` | `anchor`, `content_hash`, `path`, `source_root_id`, `summary` | — | — |
| `git_metadata` | `summary` | `commit` or `content_hash` | `path` |
| `completion_text` | `summary` | `content_hash` + `source_reference_id` or `anchor` + `content_hash` + `path` + `source_root_id` | `monitor_reference_id`, `opaque_key` |
| `result_evidence` | `summary` | `content_hash` + `source_reference_id` or `anchor` + `content_hash` + `path` + `source_root_id` | `artifact_id`, `monitor_reference_id`, `opaque_key` |
| `existing_artifact` | `artifact_id`, `summary` | — | — |
| `user_instruction` | `intent_id`, `summary` | — | — |
| `inference` | `summary`, `supporting_identities` | — | — |
<!-- END GENERATED: guided-evidence-fields -->

For `git_metadata`, satisfy one listed identity alternative. For `completion_text` and `result_evidence`, satisfy one complete listed source identity. An artifact alone is insufficient, even when its `artifact_id` is included. Git evidence remains limited to canonical-project Git.

Send no other keys. For a `source_evidence` basis, include structured evidence or source references. For `user_instruction`, include a `user_instruction` object bound to the same intent. For `inference`, include an `inference` object, obey the planning profile, and cap confidence at 0.79. These basis rules apply independently to every operation; top-level evidence does not substitute for operation evidence.

Permitted evidence families are constrained by the live contract:

- Source text under an intent-approved readable root, with normalized relative path, bounded anchor, concise summary, and truthful bounded hash.
- Bounded Git metadata from the canonical project root only.
- Explicit completion text or unambiguous result evidence.
- Existing monitor artifact metadata by UUID or canonical locator hash.
- A bound user instruction that references the matching intent.
- Explicit inference supported by permitted source or monitor identities.

Never send arbitrary dictionaries, raw excerpts, absolute paths, secrets, URL credentials, or evidence strings. Exclusion and sensitive-path rules always win. A source reference may support multiple affected tasks; preserve its exact identity rather than cloning a title-based approximation.

For direct user instruction, use `basis: "user_instruction"` and the supported evidence object referencing the bound intent. It proves completion only when the same intent has `allow_completion=true`.

For inference, use `basis: "inference"`, identify its support, keep confidence at 0.79 or lower, and obey planning-profile inference limits. Under `sources_only`, do not emit inference. Under `cautious_gaps`, create at most five inferred tasks; necessary inferred containers/edges remain labeled inference.

## Guided mode matrix

<!-- BEGIN GENERATED: guided-proposal-contract -->
| Guided mode | Allowed scope | Allowed operations | Allowed `task.update` data | Required result operation |
|---|---|---|---|---|
| `initialize_structure` | `project` | `pipeline.create`, `task.create`, `edge.create` | — | — |
| `expand_task` | `task` | `task.create`, `task.update`, `edge.create` | `description`, `priority`, `labels`, `target_date`, `completion_criteria`, `child_flow_mode` | — |
| `reconcile_progress` | `project`, `pipeline`, `task` | `task.update`, `journal.create`, `artifact.create`, `task_artifact.link` | `status`, `outcome`, `blocker_reason`, `completion_summary`, `completion_source` | — |
| `suggest_next_work` | `project`, `pipeline` | `pipeline.create`, `task.create`, `edge.create` | — | — |
| `record_update` | `task` | `task.update`, `journal.create`, `artifact.create`, `task_artifact.link` | `status`, `outcome`, `blocker_reason`, `completion_summary`, `completion_source` | `journal.create` |
| `link_artifacts` | `task` | `artifact.create`, `task_artifact.link` | — | — |

Operation bases: `inference`, `source_evidence`, `user_instruction`.
Evidence kinds: `completion_text`, `existing_artifact`, `git_metadata`, `inference`, `result_evidence`, `source_text`, `user_instruction`.
Result kinds: `changes`, `no_changes`.
No-change reasons: `ambiguous_sources`, `insufficient_evidence`, `up_to_date`.
<!-- END GENERATED: guided-proposal-contract -->

Every guided mode forbids project, planning-profile, scan-policy, root, layout, apply, delete, archive, restore, move, waiver, journal-update, and artifact-update operations. Scope, protection, projected hierarchy/DAG, completion proof, depth, task-count, atomic closure, and shared-entity rules are rechecked during validate, create, graphical revision, and apply.

Mode-specific projected-state rules narrow that generated allowlist:

- `initialize_structure` is project-only, requires an active empty monitor, and creates only pipelines and top-level `planned`/`not_applicable` tasks.
- `expand_task` creates only `planned`/`not_applicable` descendants within the selected task's pipeline. Its only editable existing-task fields are `description`, `priority`, `labels`, `target_date`, `completion_criteria`, and `child_flow_mode`.
- `reconcile_progress` updates only `status`, `outcome`, `blocker_reason`, `completion_summary`, and `completion_source`. Each reconciled journal needs stable source references so the server can derive its duplicate-resistant origin.
- `suggest_next_work` creates only top-level `planned`/`not_applicable` tasks; pipeline creation is available only at project scope. It records no progress.
- `record_update` requires exactly one `journal.create` on the selected task and therefore cannot return `no_changes`. Its task-update fields match `reconcile_progress`; completion additionally requires the intent's deliberate `allow_completion` permission and valid completion evidence.
- `link_artifacts` links only intent-explicit locators to the selected task.

Every create that references another newly created entity receives the creator operation as a prerequisite. Every new artifact and all of its required in-scope links share a nonempty atomic group.

## Agent operation types

The skill may use only operations allowed by both the live operation schema and its guided mode. The following block is generated from the backend contract:

<!-- BEGIN GENERATED: agent-operation-schemas -->
| Agent operation | `entity_id` | `expected_version` | Required `data` | Optional `data` |
|---|---|---|---|---|
| `pipeline.create` | client_generated_required | forbidden | `title` | `id`, `description`, `flow_mode`, `position` |
| `pipeline.update` | target_required | required | — | `title`, `description`, `flow_mode`, `position` |
| `pipeline.archive` | target_required | required | — | — |
| `task.create` | client_generated_required | forbidden | `pipeline_id`, `title` | `id`, `parent_id`, `user_key`, `description`, `kind`, `status`, `outcome`, `priority`, `labels`, `target_date`, `position`, `completion_criteria`, `blocker_reason`, `completion_summary`, `completion_source`, `completion_override_reason`, `child_flow_mode` |
| `task.update` | target_required | required | — | `pipeline_id`, `parent_id`, `user_key`, `title`, `description`, `kind`, `status`, `outcome`, `priority`, `labels`, `target_date`, `position`, `completion_criteria`, `blocker_reason`, `completion_summary`, `completion_source`, `completion_override_reason`, `child_flow_mode` |
| `task.move` | target_required | required | — | `pipeline_id`, `parent_id`, `position` |
| `edge.create` | client_generated_required | forbidden | `source_task_id`, `target_task_id` | `id`, `edge_type`, `disabled`, `waiver_reason` |
| `edge.update` | target_required | required | — | `edge_type`, `disabled`, `waiver_reason` |
| `journal.create` | client_generated_required | forbidden | `task_id`, `content` | `id`, `entry_type`, `occurred_at` |
| `journal.update` | target_required | required | — | `content`, `entry_type`, `occurred_at` |
| `artifact.create` | client_generated_required | forbidden | `locator` | `id`, `kind`, `artifact_root_id`, `provider`, `label`, `notes` |
| `artifact.update` | target_required | required | — | `kind`, `locator`, `artifact_root_id`, `provider`, `label`, `notes` |
| `task_artifact.link` | client_generated_required | forbidden | `task_id`, `artifact_id` | `id`, `role`, `notes` |
<!-- END GENERATED: agent-operation-schemas -->

Common meanings:

- `pipeline.create`: client ID, title, description, sequential/freeform flow, and sibling order.
- `task.create`: client ID, pipeline, optional parent, opaque key, planning fields, and sibling order.
- `task.update`: only allowed changed fields plus current entity version.
- `edge.create`: client ID, dependency/related type, and endpoints; dependency direction is prerequisite to dependent.
- `journal.create`: client ID, task ID, type, timestamp, and Markdown body. The server derives immutable origin and body hash for guided journals.
- `artifact.create`: client ID and approved-root relative locator or existing credential-free HTTP/HTTPS URL.
- `task_artifact.link`: task ID, artifact ID, role, and notes.

Never write computed readiness as task status. Reuse exact artifact identities exposed by context. Redacted locator hashes use the live documented canonical hash algorithm; do not reconstruct or expose a secret locator.

`locator_hash` is an opaque lowercase SHA-256 identity used only for equality matching. It is computed over UTF-8 canonical JSON with sorted keys and no spaces:

```text
sha256({"artifact_root_id":ROOT_UUID_OR_NULL,"kind":"local|url","locator":"EXACT_LOCATOR"})
```

An explicit intent locator context item contains `locator`, `display_locator`, `locator_hash`, and `redacted`. Use it as follows:

- First compare `locator_hash` with the artifact identity index. On an exact match, reuse the existing artifact UUID.
- If no artifact matches and `redacted` is false, copy the exact context `locator` into `artifact.create.data.locator`.
- If no artifact matches and `redacted` is true, context sets `locator` to `intent-locator:<locator_hash>`. Copy that complete token into `artifact.create.data.locator`; never send `display_locator`. The server resolves it only from the same bound intent, restores the exact locator, and revalidates its root/path or HTTP(S) URL. An invalid or foreign token is rejected. Do not derive redacted content, reuse a token across intents, treat the bare hash as a locator, or invent a separate `locator_token` field.

## Completion example

```json
{
  "id": "a5371141-195f-4933-bd1f-3e39e1d6234d",
  "type": "task.update",
  "entity_id": "a2a63362-31a0-44c8-bb72-ad1523034a30",
  "expected_version": 7,
  "prerequisite_operation_ids": [],
  "basis": "source_evidence",
  "data": {
    "status": "done",
    "outcome": "negative",
    "completion_summary": "The planned comparison completed without improving the primary metric.",
    "completion_source": "documented_result"
  },
  "rationale": "The result summary explicitly reports the completed comparison.",
  "confidence": 0.96,
  "evidence": [
    {
      "kind": "completion_text",
      "source_root_id": "a33df30b-9fb6-426a-a84b-6a0c4ab854eb",
      "path": "results/index.json",
      "anchor": "comparison-4",
      "summary": "The cited result text explicitly says the planned comparison finished with a negative outcome.",
      "content_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "source_references": [
    {
      "source_root_id": "a33df30b-9fb6-426a-a84b-6a0c4ab854eb",
      "path": "results/index.json",
      "anchor": "comparison-4",
      "opaque_key": "EXP-04",
      "fingerprint": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

A file, code path, manifest, checkpoint, link, filename, scaffold, or smoke test alone cannot prove completion. Require explicit completion text, unambiguous result proof, or a matching bound instruction with completion permission.

## Legacy v1 compatibility

An unbound v1 payload omits `proposal_contract_version`, `intent_id`, and result metadata and follows the live unqualified v1 context. It is stored as `legacy_custom` at project scope. Do not use legacy shape for a dashboard intent. Keep every legacy operation unselected and warn that typed-mode provenance is unavailable.

The skill must not propose `project.*`, `planning_profile.*`, `scan_policy.*`, `artifact_root.*`, `layout.*`, apply, restore, purge, or ordinary destructive operations in either contract.

Successful validation proves schema and domain consistency, not factual correctness. Human graphical review remains mandatory.
