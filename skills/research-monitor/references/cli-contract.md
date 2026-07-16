# Research Monitor agent CLI contract

Use this reference for command spelling, transport behavior, and error handling. Use the `proposal_contract` in live agent context for the exact operation/data schema supported by the installed application.

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

Do not scrape human-formatted output. Reject an envelope with an unsupported API or schema version.

## Commands

<!-- BEGIN GENERATED: stable-cli-commands -->
```text
research-monitor version --json
research-monitor open [--no-open] [--json]
research-monitor project list --json
research-monitor project resolve --path PATH --json
research-monitor agent context --project UUID --json
research-monitor proposal validate --project UUID --file FILE_OR_-
research-monitor proposal create --project UUID --file FILE_OR_-
research-monitor proposal inspect PROPOSAL_ID --json
research-monitor export project --project UUID [--output PATH]
research-monitor backup create [--output PATH] [--force]
research-monitor backup restore PATH --confirm
research-monitor skill status
research-monitor skill install [--force]
research-monitor skill update [--force]
```
<!-- END GENERATED: stable-cli-commands -->

For skill-driven maintenance, use only `version`, `project resolve`, `agent context`, `proposal validate`, `proposal create`, and `proposal inspect`. The dashboard open, export, backup, restore, and skill-management commands are documented for users but are outside the reconciliation workflow.

Pass `-` to proposal `--file` to read exactly one UTF-8 JSON object from standard input. Validation does not persist a proposal. Creation persists a draft for human review; it does not apply operations.

## Agent context

Successful `agent context` data contains:

```text
project                 identity, canonical root, availability, semantic_revision
scan_policy             preferred sources, globs, limits, Git policy, sensitive paths
artifact_roots          roots already approved by a human
pipelines               canonical pipeline snapshot and entity versions
tasks                   canonical task snapshot and entity versions
edges                   dependency/related edges and waivers
artifacts               existing artifact locators and task associations
source_references       prior identities, anchors, and fingerprints
proposal_contract       supported operation types and their data requirements
```

Journal bodies are excluded by default. Do not infer that an absent body or source was deleted. Request only capabilities exposed by the CLI; do not read the database.

## Project resolution

Resolution takes an absolute or relative path, canonicalizes it safely, and selects the deepest enrolled project root containing it. Treat not-found, unavailable, and ambiguous responses as terminal until a human resolves them in the UI. Never enroll or relink from the skill.

## Git metadata safety

Inspect Git metadata only when the returned scan policy explicitly permits it. Substitute the canonical project root, a numeric history limit no larger than the policy limit, and exact policy-authorized pathspecs in these command shapes:

```text
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT status --short --untracked-files=no --ignore-submodules=all
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT log --max-count=LIMIT --format=%H%x09%ct%x09%an%x09%s --
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT diff --no-ext-diff --no-textconv --ignore-submodules=all --stat --
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT diff --cached --no-ext-diff --no-textconv --ignore-submodules=all --stat --
git --no-optional-locks --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -C ROOT ls-files --stage -- PATHSPEC
```

Pass substituted values as safely quoted arguments and impose the tool's time and output limits. The global `--no-optional-locks` flag is mandatory on every invocation because nominally read-only commands may otherwise refresh or lock the index. Disabling the pager, fsmonitor, hooks, external diffs, text conversion, and submodule traversal prevents repository configuration from turning inspection into project execution. Capture output directly from the tool; never use output files, shell redirection, or `tee` in or beneath an enrolled root.

Do not run any other Git subcommand. In particular, never add, commit, switch, checkout, reset, clean, stash, fetch, pull, push, merge, rebase, update the index, run maintenance, or invoke a repository alias or hook. Never inspect `.git` files directly.

## Exit codes

| Code | Meaning | Required response |
|---:|---|---|
| 0 | Success | Continue after validating the envelope. |
| 2 | Invalid input or schema | Correct the payload; do not bypass validation. |
| 3 | Project not found or path ambiguous | Ask the user to select/enroll/relink in the UI. |
| 4 | Semantic revision conflict | Fetch fresh context and regenerate affected operations. |
| 5 | CLI/API/database incompatibility | Stop and report the installed versions. |
| 6 | Server, lock, or transport unavailable | Retry the identical request once; retain its request UUID. |

Any other nonzero exit is terminal. Do not retry a changed payload with the old request UUID.

## Coordination and safety

The CLI uses the authenticated local API while the server owns the database lock and the shared domain service when it can safely acquire an offline lock. Do not manipulate runtime descriptors, locks, tokens, or SQLite files. Do not call HTTP endpoints directly. Never use export, backup, or restore as a substitute for a proposal.
