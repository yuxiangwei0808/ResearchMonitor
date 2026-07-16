# Research Monitor

Research Monitor is a free, local-first dashboard for planning and recording work across research projects that you explicitly enroll. It combines editable pipelines, arbitrarily nested tasks, dependency graphs, journals, artifact links, audit history, and reviewable Codex proposals. It does not embed an LLM and never writes to enrolled research folders.

## Install and run

The release wheel contains the compiled web interface and companion skill. Routine use needs Python 3.12 and `uv`; Node.js is only needed to build the frontend from source.

```bash
uv tool install /path/to/research_monitor-0.1.0-py3-none-any.whl
research-monitor serve --open
```

Stop a running monitor gracefully, or replace it in one command:

```bash
research-monitor stop
research-monitor serve --force-restart --open
```

`--force-restart` authenticates the recorded server instance, requests a graceful shutdown, waits for its host-local lock, and then reacquires the host-local and shared writer locks in order while starting the replacement. With no explicit `--port`, it reuses the running server's port. It never attempts to stop an instance on another host: if the shared writer lock is owned elsewhere, startup fails closed and reports bounded owner metadata. A server started by a release that predates these commands must be stopped once with `Ctrl+C` in its original terminal before upgrading.

The server binds only to `127.0.0.1`. The default address is `http://127.0.0.1:8765`; set `RESEARCH_MONITOR_PORT` to choose another port.

For VS Code Remote, start `research-monitor serve --port 8765`, forward
remote port 8765 to local port 8765, and use VS Code's external **Open in
Browser** action—not **Preview in Editor** or Simple Browser. A user-launched
top-level visit to either `http://localhost:8765/` or
`http://127.0.0.1:8765/` automatically establishes the protected browser
session. Reopening the same address also works for a fresh browser profile or
after its cookies are cleared. Port probes, embedded pages, cross-site
navigation, assets, and API requests cannot create a session.

Browser sessions last only for the browser session and the current server process. Reopen the bare dashboard address to establish a fresh one. As a compatibility or recovery fallback, mint a 60-second, one-use login URL with the owner-authenticated CLI:

```bash
research-monitor open
```

Use `research-monitor open --no-open` to print the URL without launching a browser.

From the dashboard, add only the project folders you want to monitor. A newly enrolled project is intentionally empty: create a pipeline/task manually or copy its Codex initialization prompt.

## Typical workflow

1. Enroll a project folder from the portfolio.
2. Define pipelines and nested tasks in Outline, or ask Codex to draft a proposal.
3. Reorder, move, edit, archive, delete, and restore work graphically.
4. Connect dependencies and related work in Graph. A dependency may be waived only with a recorded reason.
5. Record progress, decisions, blockers, outcomes, completion evidence, and artifact associations.
6. Inspect every Codex operation in Proposals. Select or edit operations, then apply the dependency-closed selection atomically.

In Outline, click a task title to open the full editor. Use the visible `…` menu—or right-click/long-press the task row—for Edit, Add subtask, and Delete. Deletion is recoverable from Deleted items. Target date is editable; Created, Last updated, and Completed timestamps are recorded automatically.

Graph opens on the first pipeline and displays only its top-level tasks. Select another pipeline or All pipelines. Hover over a parent task for a read-only preview of its immediate subtasks; keyboard focus opens the same preview, and Escape closes it. A single click only selects and highlights a task. Double-click a parent, press Enter or Space while it is focused, or click its **X subtasks** control to drill into the next hierarchy level. On a leaf, those navigation gestures do nothing beyond leaving it selected. Use the breadcrumb or **Up one level** to return. Editing is available only through the graph card's `…` menu or its right-click/long-press menu.

W&B and MLflow entries are stored as artifact links. Research Monitor does not fetch their data or replace experiment-monitoring tools.

## Companion Codex skill

Install or inspect the bundled skill with:

```bash
research-monitor skill status
research-monitor skill install
research-monitor skill update
```

The skill resolves an enrolled project, reads its human-configured scan policy, inspects permitted text and bounded Git metadata read-only, and submits a structured proposal. It cannot enroll/relink projects, approve roots, change scan policy, execute project code, use the network, modify research files, or apply its own proposal.

Stable agent commands include:

```bash
research-monitor version --json
research-monitor project resolve --path /absolute/project/path --json
research-monitor agent context --project PROJECT_UUID --json
research-monitor proposal validate --project PROJECT_UUID --file proposal.json
research-monitor proposal create --project PROJECT_UUID --file proposal.json
research-monitor proposal inspect PROPOSAL_UUID --json
```

## Backups and recovery

```bash
research-monitor backup create
research-monitor backup restore /path/to/monitor-backup.db --confirm
```

Backups use SQLite's online backup API and must pass `PRAGMA integrity_check`. Restore requires the server to be stopped, validates the candidate's schema and integrity before replacement, and normally creates a fresh verified pre-restore backup. If corruption makes that backup impossible, restore first preserves the exact SQLite main database plus any WAL, SHM, and rollback-journal sidecars in an owner-only `forensics/pre-restore-*` directory with a private manifest containing the reason, source path, timestamp, sizes, and SHA-256 hashes. The CLI reports either recovery location in its structured result. Permanently purging a trashed project also creates a verified backup first:

```bash
research-monitor project purge PROJECT_UUID --confirm PROJECT_UUID
```

Archiving, trashing, purging, relinking, and restoring monitor state never modify the research folder.

## Data and security

Default Linux/XDG locations are:

- Database: `$XDG_DATA_HOME/research-monitor/monitor.db`
- Cross-host writer lock: `$XDG_DATA_HOME/research-monitor/writer.lock`
- Configuration: `$XDG_CONFIG_HOME/research-monitor/config.toml`
- Host-local runtime descriptor, CLI token, and application lock: `$XDG_RUNTIME_DIR/research-monitor/`

The monitor is a single-writer SQLite application. It uses rollback-journal `DELETE` mode with `synchronous=FULL` so the default database remains safe when a home directory is hosted on NFS; WAL shared-memory coordination is not used. Normal startup performs read-only integrity and schema checks before database access and refuses to write when either check fails, with an actionable structured recovery error. If startup finds a preserved hot rollback journal, it first retains an owner-only forensic copy, lets SQLite perform a controlled rollback, and then repeats integrity and foreign-key checks before proceeding.

The server holds both the host-local application lock and database-adjacent shared writer lock for its lifetime. Offline commands acquire them in that order, including commands that only read monitor data. If the local lock is held, the CLI routes through the verified server on that host. If the local lock is free but the shared lock is held, it does not touch the database; it returns `shared_writer_active` with bounded hostname, PID, process-start tick, and acquisition-time metadata when available.

For one database shared across Linux login hosts, keep `XDG_RUNTIME_DIR` host-local and place `XDG_DATA_HOME` on the shared filesystem. Only one host may serve or access the monitor offline at a time, and the NFS mount must honor Linux cross-host advisory locks. Host-local `stop` and `--force-restart` commands do not seize or terminate a remote host's instance.

If an XDG variable is absent, Research Monitor uses an owner-only fallback beneath the user data directory. Set `RESEARCH_MONITOR_HOME=/path/to/isolated-home` for tests or temporary monitors; that override intentionally places data and runtime state together. Set `RESEARCH_MONITOR_ALLOWED_ROOTS` to a colon-separated allowlist when enrollment should be narrower than the home directory.

Local artifacts are referenced by approved-root UUID plus relative path. Every access repeats realpath and symlink-containment checks. Secret-like paths and unsafe formats are metadata-only; external artifacts accept only HTTP/HTTPS and are never fetched.

Project and additional artifact roots must remain separate from Research Monitor's data, configuration, runtime, database, and managed backup paths. The application rejects either direction of overlap so enrolling a broad parent folder cannot cause backups or runtime files to be written into research storage.

The localhost threat model protects against malicious browser pages and untrusted project content, but not another process already running as the same OS user.

## Development

Requirements:

- Python 3.12 and `uv`
- Node.js 24 and npm for frontend work

Install and test the backend:

```bash
uv sync --extra dev
uv run pytest tests/backend
uv run pytest tests/skill
```

The backend suite builds a wheel into a temporary directory, verifies that its
Python modules, compiled frontend, and four-file companion skill exactly match
the source trees, then installs it into an isolated environment. The smoke test
runs the installed CLI, skill installer, browser bootstrap, packaged dashboard,
API, and fresh SQLite database with Node absent from `PATH`.

Run the backend and Vite development server in separate terminals:

```bash
uv run research-monitor serve
```

```bash
cd frontend
npm ci
npm run dev
```

Build and test release assets:

```bash
cd frontend
npm run build
npm test
npx playwright install chromium
npm run test:e2e
cd ..
uv run python scripts/generate_skill_contracts.py --check
uv run python /path/to/skill-creator/scripts/quick_validate.py skills/research-monitor
uv build
```

The Playwright suite rebuilds the Vite production assets, starts the real
`research-monitor serve` FastAPI process with a unique temporary monitor home
and SQLite database, opens the bare dashboard URL in multiple fresh browser
contexts, and drives Chromium against that server. It enrolls temporary on-disk projects and
uses the authenticated agent API only to submit proposal fixtures; UI reads and
mutations still cross the real HTTP, CSRF, domain-service, and persistence
boundary. It does not use the Vite development server, mocked requests, or an
installed wheel.

The wheel build fails when the compiled frontend or validated bundled skill is absent. No CDN or hosted service is used at runtime.
It also fails for missing or stale frontend references, unexpected or missing
skill files, malformed `SKILL.md` frontmatter or `agents/openai.yaml` metadata,
and generated CLI/change-set reference blocks that differ from the backend
contract definitions. Reused build directories are cleaned before verified
asset copies, preventing obsolete hashed assets from leaking into a wheel.
