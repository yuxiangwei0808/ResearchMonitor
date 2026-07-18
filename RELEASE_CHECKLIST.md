# Research Monitor v0.2.0 Release Checklist

This checklist is for release maintainers, not everyday users. Start with the [README](README.md) for installation, VS Code Remote forwarding, manual planning, and optional Codex use.

This checklist is intentionally not a record of passing results. Mark each item only after running it against the final source commit and release artifacts. Record exact counts, environment details, commit/tag, filenames, and SHA-256 digests in an external release record; do not embed wheel/sdist digests here because changing this file changes those artifacts.

The release record should include: source commit/tag, timestamp, Python/uv/Node/npm/browser versions, migration source and target revisions, each command and result count, artifact filenames/sizes/SHA-256 digests, pre-upgrade backup path, optional-skill state, and the synthetic-agent-gate fixture hashes. Keep browser bootstrap URLs, CLI tokens, session cookies, and raw synthetic-agent traces out of that record.

## Source and migration gate

- [ ] Confirm Python package metadata, CLI version output, and frontend package/display metadata report `0.2.0`, and the installed bundled-skill hash matches that wheel.
- [ ] Confirm the worktree contains no unintended files and the bundled skill contains exactly its four expected files.
- [ ] Stop the v0.1 server and create a verified SQLite backup through `research-monitor backup create`.
- [ ] Upgrade a copy of a released 0004 database through `0005_guided_agent_workflows`.
- [ ] Verify pre-0005 validators remain frozen, the complete v0005 validator passes, foreign keys pass, and a partially applied or malformed migration fails closed.
- [ ] Verify a failed migration leaves the original usable or recoverable from the automatically verified pre-migration backup.
- [ ] Verify rollback while v0.2 is still installed with `research-monitor backup restore <pre-0005.db> --confirm --rollback-to-v0.1`; confirm the database remains at revision 0004, then reinstall v0.1 before any restart. Never run v0.1 against a migrated database or restart v0.2 after the preserving restore.

## Automated gates

Run these commands from a clean checkout with the final dependency lockfiles:

```bash
uv sync --extra dev
uv run pytest tests/backend
uv run pytest tests/skill
cd frontend
npm ci
npm test
npm run build
npx playwright install chromium
npm run test:e2e
cd ..
uv run python scripts/generate_skill_contracts.py --check
uv run python /path/to/skill-creator/scripts/quick_validate.py skills/research-monitor
```

- [ ] Backend and migration suite passed; record count and duration.
- [ ] Frontend unit/component suite passed; record count and duration.
- [ ] Production-bundle Playwright suite passed; record count, browser, and duration.
- [ ] Companion-skill suite and official `quick_validate.py` passed.
- [ ] Generated CLI/change-set references match backend-owned definitions.
- [ ] Wheel and sdist build from the final commit without stale frontend or skill assets.

Build the distribution twice from independent candidate copies. Use the same
source-tree basename in both copies, clear local build products before copying,
and fix the archive epoch. Do not compare artifacts produced from the working
checkout with artifacts produced from an exported copy.

```bash
export SOURCE_DATE_EPOCH=1767225600
BUILD_ROOT_A="$(mktemp -d /tmp/research-monitor-build-a.XXXXXX)"
BUILD_ROOT_B="$(mktemp -d /tmp/research-monitor-build-b.XXXXXX)"
mkdir -p "$BUILD_ROOT_A/research-monitor" "$BUILD_ROOT_B/research-monitor"
rsync -a --delete \
  --exclude .git --exclude .venv --exclude node_modules \
  --exclude '/dist/' --exclude '/build/' --exclude '*.egg-info' \
  ./ "$BUILD_ROOT_A/research-monitor/"
rsync -a --delete \
  --exclude .git --exclude .venv --exclude node_modules \
  --exclude '/dist/' --exclude '/build/' --exclude '*.egg-info' \
  ./ "$BUILD_ROOT_B/research-monitor/"
(cd "$BUILD_ROOT_A/research-monitor" && uv build --out-dir ../dist)
(cd "$BUILD_ROOT_B/research-monitor" && uv build --out-dir ../dist)
sha256sum "$BUILD_ROOT_A"/dist/*
sha256sum "$BUILD_ROOT_B"/dist/*
diff \
  <(cd "$BUILD_ROOT_A/dist" && sha256sum * | sed 's/  .*/  ARTIFACT/') \
  <(cd "$BUILD_ROOT_B/dist" && sha256sum * | sed 's/  .*/  ARTIFACT/')
```

- [ ] Both wheels are byte-identical and both sdists are byte-identical; record
  the common SHA-256 digests, artifact sizes, fixed epoch, and Python, uv,
  setuptools, wheel, Node, and npm versions in the release record.

## Guided automation acceptance

- [ ] Issue and consume each mode: initialize structure, expand task, reconcile progress, suggest next work, record update, and link artifacts.
- [ ] Reject forged, expired, consumed, cross-project, scope-mismatched, and semantically stale intents while allowing layout-only changes.
- [ ] Return the original result for an exact request retry and reject changed content under the bound request UUID.
- [ ] Produce and display both a reviewable `changes` proposal and a closed `no_changes` result.
- [ ] Revalidate mode, scope, protection, completion proof, hierarchy/DAG, depth, task-count, prerequisite closure, and atomic artifact linking during validation, creation, graphical revision, and apply.
- [ ] Confirm inferred, completion, risky, and legacy operations are textually labeled and start unselected.
- [ ] Confirm a `record_update` selection cannot omit its required journal and repeated accepted source identity cannot create a duplicate journal.
- [ ] Confirm shared artifact metadata cannot be changed by a guided scoped proposal.
- [ ] Confirm legacy v1 proposal create/revise/apply/reject retries retain their released fingerprints and are labeled `legacy_custom`.

## Privacy and context acceptance

- [ ] Confirm additional artifact-root approval alone never grants Codex read access; explicit readable-source selection is required.
- [ ] Reject traversal, escaping/replaced symlinks, unavailable roots, excluded or sensitive paths, oversized files, total file/byte budget overflow, unsafe URL schemes, URL credentials, and suspicious credential query parameters.
- [ ] Confirm Git inspection remains bounded to the canonical root and only the five allowlisted non-locking command shapes.
- [ ] Confirm scoped context contains necessary ancestors, boundary dependencies, full-graph readiness, deterministic truncation metadata, and no journal bodies, artifact previews, secret locators, or unrelated task descriptions.
- [ ] Confirm no application, CLI, test, or skill operation modifies an enrolled research file or fetches an external artifact.
- [ ] Run deterministic offline skill-contract tests on synthetic temporary repositories; do not label hand-constructed proposal fixtures as real-agent forward tests.
- [ ] Separately run the manual release gate with fresh Codex processes, monitor homes, and Codex homes using synthetic fixtures only. Record any OpenAI/network use, hash fixture trees before and after, and confirm no execution or mutation. Any live enrolled-project scan requires fresh explicit authorization.

## Browser and installed-wheel acceptance

- [ ] Complete all six Ask Codex flows using keyboard-only interaction, including copy denial recovery, focus restoration, textual risk labels, apply confirmation, and rejection dialog.
- [ ] Verify authentication recovery and disconnected-state behavior through both `localhost` and `127.0.0.1` VS Code port forwarding.
- [ ] Install the wheel and its declared dependencies normally into a fresh isolated Python environment with `PYTHONNOUSERSITE=1`, cleared `PYTHONPATH`, no development `.pth` bridge, and Node absent from `PATH`.
- [ ] With the optional skill still missing, start the packaged server, establish a fresh browser session, load compiled assets, create a temporary project, issue an intent, validate/create a synthetic result, inspect it graphically, and restart the server.
- [ ] In a separate isolated `CODEX_HOME`, explicitly install/update the optional skill and verify exact packaged contents and Current status.
- [ ] Confirm install/update require a stopped monitor and retain both monitor data locks through replacement. Reject overlap with active, archived, and trashed project roots and additional artifact roots, including every managed installer path, symlink, and both containment directions; confirm `--force` cannot bypass the guard and concurrent installers return `skill_install_busy`.
- [ ] Verify a user-modified installed skill is backed up before forced update and the previous valid skill remains after any failed update or interrupted swap; verify orphan recovery restores a valid destination on the next invocation.
- [ ] Verify installer source and backup trees reject symlinks or special nodes that could escape into an enrolled project, and that a custom directory or other unexpected entry is detected as a modification before replacement.
- [ ] Verify unreadable or malformed installed-skill state is reported as `Blocked` with corrective guidance rather than an unstructured failure.
- [ ] From a clean installed wheel on a remote Linux host, exercise the README's VS Code Ports sequence in an external browser: bare forwarded address, fresh-session recovery, port change, and `research-monitor open --no-open` fallback.

## Recovery, scale, and artifact record

- [ ] Create and restore a verified backup through SQLite's online backup API; run integrity and schema checks before and after restore.
- [ ] Re-run writer-exclusion and NFS recovery scenarios. Record the mount and host limitations without expanding the v0.1 certification claims.
- [ ] Exercise at least 20 projects, 2,000 tasks, 5,000 artifact identities, proposal-summary pagination, scoped context caps, and persistent event replay without loading every artifact body.
- [ ] Record source tag/commit, Python/uv/Node/npm/browser/build-tool versions, migration source database version, artifact names, sizes, and SHA-256 digests externally.
- [ ] Preserve the verified pre-upgrade backup until v0.2 acceptance is complete.

## Prior v0.1.0 baseline evidence

The following results were recorded on 2026-07-16 for v0.1.0. They are historical context, not evidence that v0.2.0 passes.

| Area | Reproducible command or scenario | Recorded result |
| --- | --- | --- |
| Backend | `uv run pytest tests/backend` | 233 passed |
| Frontend unit/component | `cd frontend && npm test -- --run` | 87 passed |
| Browser end-to-end | `cd frontend && npm run test:e2e` | 5 passed against the production FastAPI/Vite bundle |
| Companion skill | `uv run pytest tests/skill` | 9 passed |
| NFS recovery | Install the built wheel into an isolated monitor home on the actual NFS mount, create a backup, apply a monitor-only mutation, and restore | Passed; integrity, schema, and pre-mutation state were verified |
| Writer exclusion | Hold the database-adjacent writer lock, then access the same data directory through a distinct runtime directory | Passed closed with `shared_writer_active` before database access |
| Distribution contents | `uv build`, then inspect sdist and wheel with `tests/backend/test_installed_wheel.py` | Python, frontend, skill, and MIT license inclusion were validated |

Historical limits:

- Crash, backup, mutation, and restore used a real NFS filesystem from one host, not two hosts.
- Writer-lock testing used separate runtime directories on one host; cross-host coordination remains conditional on advisory-lock behavior.
- The tested mount reported `soft,local_lock=none`; v0.1 did not certify it for a shared two-host database.
- No deployment-level `kill -9` test was claimed; subprocess hot-journal coverage was narrower.
