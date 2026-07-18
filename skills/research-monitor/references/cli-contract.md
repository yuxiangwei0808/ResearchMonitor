# Research Monitor agent CLI contract

Use this reference for command spelling, transport behavior, and error handling. Use `proposal_contract` in live agent context for the exact intent, evidence, result, operation, and data schemas supported by the installed application.

## Common response envelope

Every command that supports `--json` returns one JSON object on standard output:

```json
{
  "api_version": "1",
  "schema_version": "1",
  "request_id": "6f142f62-3b36-44ea-a042-5366a3702522",
  "data": {}
}
```

Failures use the same version and request fields plus:

```json
{
  "error": {
    "code": "project_not_found",
    "message": "No enrolled project contains the path",
    "details": {}
  }
}
```

Do not scrape human-formatted output. Reject unsupported API/schema versions or missing capabilities. Guided v2 prompts require version capabilities named by the prompt, including `guided_agent_intents`, `proposal_contract`, `scoped_agent_context`, and `no_change_results`.

## Commands

<!-- BEGIN GENERATED: stable-cli-commands -->
```text
research-monitor version --json
research-monitor open [--no-open] [--json]
research-monitor project list --json
research-monitor project resolve --path PATH --json
research-monitor agent context --project UUID --json
research-monitor agent context --project UUID --intent UUID --json
research-monitor proposal validate --project UUID --file FILE_OR_-
research-monitor proposal create --project UUID --file FILE_OR_-
research-monitor proposal inspect PROPOSAL_ID --json
research-monitor export project --project UUID [--output PATH]
research-monitor backup create [--output PATH] [--force]
research-monitor backup restore PATH --confirm [--rollback-to-v0.1]
research-monitor skill status
research-monitor skill install [--force]
research-monitor skill update [--force]
```
<!-- END GENERATED: stable-cli-commands -->

The generated block is authoritative for exact installed spelling. The guided context form is:

```text
research-monitor agent context --project UUID --intent UUID --json
```

For guided maintenance, use only `version`, intent-bound `agent context`, `proposal validate`, `proposal create`, and `proposal inspect`. Use `project resolve` and unqualified context only for warned legacy reconciliation. Dashboard, export, backup, restore, and skill-management commands are user operations outside the agent workflow.

Pass `-` to proposal `--file` to read exactly one UTF-8 JSON object from standard input. Validation does not persist a result. Creation persists either a reviewable changes draft or a closed no-change check; it never applies operations.

## Intent-bound context

The browser issues an immutable, expiring intent and places its project and intent UUIDs in the copied prompt. Do not mint an intent, call browser endpoints, or replace prompt claims with agent-selected values.

Successful intent-bound context includes:

```text
project                 identity, canonical root, availability, semantic_revision
intent                  UUID, bound request UUID, mode, scope, expiry, completion permission
planning_profile        granularity, horizon, inference and enforced structural limits
scan_policy             readable roots, globs, file/byte limits, Git policy, sensitive paths
scope                    writable entities with full versions plus read-only boundary stubs
readiness                values computed from the complete active project graph
identity_indexes         compact accepted sources, artifacts, journal hashes, open drafts
proposal_contract       v2 result, evidence, mode, scope, and operation schemas
```

Compact collections report `items`, `total`, `limit`, and `truncated` in deterministic order. Journal bodies, artifact previews, secret locators, and unrelated task descriptions are excluded. Never infer absent content was deleted. If a required identity index is truncated, omit uncertain creates or ask the user to narrow the dashboard scope.
Intent `explicit_artifact_locators` and the project artifact identity index expose deterministic `locator_hash` values. Compare them to reuse an existing artifact UUID. Each explicit item also has `redacted`, safe `display_locator`, and `locator`: the exact locator when unredacted, or an intent-bound `intent-locator:<locator_hash>` token when redacted. Copy that token only to `artifact.create.data.locator` for the same intent; never send the display value, bare hash, or reconstructed secret. The server resolves and revalidates the exact locator before accepting the operation.


The intent is bound to its issued semantic revision and planning-profile version. A layout-only change does not stale it. Stop on `intent_stale`, `intent_expired`, `intent_consumed`, project mismatch, or scope ineligibility and ask for a fresh dashboard prompt.

## Legacy project resolution

Legacy resolution takes an absolute or relative path, canonicalizes it safely, and selects the deepest enrolled project root containing it. Treat not-found, unavailable, and ambiguous responses as terminal until a human resolves them in the UI. Never enroll or relink from the skill.

Unqualified `agent context --project UUID --json` preserves the v1 compatibility path. Warn that it lacks intent provenance and typed-mode enforcement. Never present a legacy proposal as guided v2 work.

## Git metadata safety

Inspect Git metadata only when the returned scan policy explicitly permits it. Substitute the canonical project root, a numeric history limit no larger than policy, and exact authorized pathspecs in these command shapes:

```text
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT status --short --untracked-files=no --ignore-submodules=all
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT log --max-count=LIMIT --format=%H%x09%ct%x09%an%x09%s --
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT diff --no-ext-diff --no-textconv --ignore-submodules=all --stat --
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT diff --cached --no-ext-diff --no-textconv --ignore-submodules=all --stat --
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT ls-files --stage -- PATHSPEC
```

Pass substituted values as safely quoted arguments and impose tool time/output limits. The global `--no-optional-locks` flag is mandatory because nominally read-only commands may otherwise refresh or lock the index. Disabling pager, fsmonitor, hooks, external diffs, text conversion, and submodule traversal prevents repository configuration from turning inspection into execution. Capture output directly; never redirect it into an enrolled root.

Do not run any other Git subcommand. Never add, commit, switch, checkout, reset, clean, stash, fetch, pull, push, merge, rebase, update the index, run maintenance, invoke aliases or hooks, or inspect `.git` files directly.

## Exit codes and guided failures

| Code | Meaning | Required response |
|---:|---|---|
| 0 | Success | Continue after validating the envelope. |
| 2 | Invalid input or schema | Correct truthful payload errors; never bypass validation. |
| 3 | Project not found or path ambiguous | Ask the user to select, enroll, or relink in the UI. |
| 4 | Semantic revision or intent conflict | Ask for a fresh dashboard intent; never replay stale operations. |
| 5 | CLI/API/database incompatibility | Stop and report installed versions and capabilities. |
| 6 | Server, lock, or transport unavailable | Retry one identical request; retain its bound request UUID. |

Any other nonzero exit is terminal. Structured intent errors take precedence over generic retry advice. Never retry changed content with the bound request UUID.

## Coordination and safety

The CLI uses the authenticated local API while the server owns the database lock and the shared domain service when it can safely acquire an offline lock. Do not manipulate runtime descriptors, locks, tokens, SQLite files, or HTTP endpoints. Never use export, backup, or restore as a substitute for a proposal.
