# Research Monitor

Research Monitor is a free, local-first web app for planning and recording research work across the folders you explicitly choose to enroll. It gives each project editable pipelines, arbitrarily nested tasks, a focused dependency graph, journals, artifact links, audit history, and optional reviewable Codex proposals.

It is designed for one researcher on a Linux host (including a remote Linux machine used through VS Code). It needs no account, hosted database, Docker, or Node.js at runtime. It is a research-task monitor—not an experiment dashboard: W&B and MLflow can be linked as artifacts, but Research Monitor does not fetch or visualize their metrics.

Research Monitor keeps its data in a central SQLite database. It never writes to, moves, executes, or otherwise changes an enrolled research folder.

## Start here

### What you need

- A Linux host with Python 3.12 and [`uv`](https://docs.astral.sh/uv/).
- A released `research_monitor-0.2.0-py3-none-any.whl` wheel. Obtain it from this project's release page, your administrator, or a locally shared release-artifact directory; `/path/to/...whl` below deliberately means that local wheel path. Contributors can build one from source as described in [Development](#development).
- A normal web browser. When using VS Code Remote from Windows, use VS Code's **Ports** panel to open the browser on your local machine.

Check the two runtime prerequisites:

```bash
python3 --version
uv --version
```

Install the wheel, then confirm the command is available:

```bash
uv tool install /path/to/research_monitor-0.2.0-py3-none-any.whl
research-monitor version --json
```

If the shell says `research-monitor: command not found`, let uv add its tool-executable directory to your shell path, then open a new terminal:

```bash
uv tool update-shell
```

To upgrade an existing installation, stop the monitor first and add `--force`:

```bash
research-monitor stop
uv tool install --force /path/to/research_monitor-0.2.0-py3-none-any.whl
```

### Open the dashboard

On a local Linux desktop, this is usually enough:

```bash
research-monitor serve --open
```

For a remote Linux host in VS Code, start a chosen remote port instead. `--open` may try to open a browser on the remote host, so it is not needed here:

```bash
research-monitor serve --port 8765
```

Then:

1. Open VS Code's **Ports** panel and forward remote port `8765`.
2. Select its **Open in Browser** action. Use the **Forwarded Address** that VS Code supplies; its local port can differ from `8765`.
3. Open that address in a normal external browser tab, not **Simple Browser** or **Preview in Editor**.

A user-initiated visit to the bare forwarded address automatically creates the protected browser session. If port `8765` is busy or times out, choose another unused remote port, for example `9013`, restart with `--port 9013`, and forward that same remote port.

If the browser still asks you to authenticate, first reopen the bare forwarded address in a normal browser. As a recovery fallback, run this on the Linux host:

```bash
research-monitor open --no-open
```

It prints a one-use URL valid for 60 seconds. Treat that URL like a password and do not share it. When VS Code assigned a different local port, do **not** paste the printed remote `127.0.0.1:PORT` origin into the Windows browser. Keep only its `/__bootstrap/...` path and append it to the VS Code Forwarded Address, for example `http://localhost:LOCAL_PORT/__bootstrap/...`.

### Create your first project

1. On the **Portfolio** page, select **Add project**.
2. Enter a name and an absolute **Linux** folder path, such as `/home/me/research/my-study`. Do not enter a Windows path such as `C:/Users/you/research/my-study` when the monitor runs remotely.
3. Select **Add project**. The project starts empty on purpose.
4. Open **Outline**, choose **Create pipeline**, then choose **Create task**. Add subtasks as needed.

You can also enroll an existing folder from the terminal:

```bash
research-monitor project add /home/me/research/my-study --name "My study"
```

An enrolled root must exist, be an absolute Linux directory, and be inside an allowed workspace root. By default, the allowed area is your home directory. Before starting the server, narrow or extend it with a colon-separated list if needed:

```bash
RESEARCH_MONITOR_ALLOWED_ROOTS=/home/me:/mnt/research research-monitor serve --port 8765
```

That form applies only to the command it prefixes. If you also use offline CLI commands in another terminal, export the value before starting the monitor (or place it in your shell profile):

```bash
export RESEARCH_MONITOR_ALLOWED_ROOTS=/home/me:/mnt/research
research-monitor serve --port 8765
```

## Learn the model in two minutes

| Term | Meaning |
| --- | --- |
| **Project** | One explicitly enrolled research folder. You can archive or remove its monitor data without changing that folder. |
| **Pipeline** | A group of tasks. It is either **Sequential** (adjacent tasks imply order) or **Freeform** (no automatic order). |
| **Task** | A piece of work, milestone, or gate. Tasks can be nested to any practical depth within their pipeline. |
| **Dependency** | An explicit prerequisite that affects readiness. A **related** edge only records a connection and never blocks work. |
| **Status** | Your workflow state: planned, in progress, blocked, review, done, or dropped. A blocked task needs an explanation. |
| **Readiness** | A computed signal: ready, waiting, blocked, or inconsistent. It is not a status you edit. |
| **Outcome** | The scientific result of completed work. A done experiment can still be negative, failed, or inconclusive. |
| **Artifact** | A safe reference to code, a result file, a document, a run URL, or other evidence. The monitor does not fetch remote artifacts. |
| **Proposal** | A reviewable set of suggested changes from Codex. Nothing is applied until you select and confirm it. |

## Everyday use

### Plan and edit work

Start in **Outline** for most edits:

- Create one or more pipelines, then add top-level tasks and subtasks.
- Select a task title to open its full editor. Use the visible `…` menu—or right-click/long-press a task row—for Edit, Add subtask, and Delete.
- Deletion is recoverable from **Deleted items**. Archive a project when you want to hide it without deleting its monitor history.
- Record target dates, priorities, labels, completion criteria, blocker explanations, and journal entries as you go.
- Mark a task done only when its completion criteria are met. The monitor records the time, actor, evidence, and optional scientific outcome.

Use **Overview** for counts and recent recorded activity; use **Artifacts** to link local results, code, papers, W&B runs, MLflow runs, and dashboards; use **Activity** for the audit trail. “Recent activity” means recorded monitor activity—Research Monitor does not watch or infer filesystem changes.

### Use the graph without losing the hierarchy

**Graph** shows one hierarchy level at a time so a large plan stays readable.

- Choose a pipeline or **All pipelines** to see top-level tasks.
- Hover over, or keyboard-focus, a parent to preview its immediate subtasks. The preview is read-only.
- A single click selects a card. Double-click a parent, press Enter or Space while it is focused, or select **X subtasks** to drill into its children.
- Use breadcrumbs or **Up one level** to return. On a leaf task, navigation gestures only keep it selected.
- Edit only through the graph card's `…` menu or its right-click/long-press menu. This prevents accidental edits while navigating.

Dependencies, related edges, sequential order, and readiness remain synchronized between Outline and Graph. Collapsed parents indicate connections involving hidden descendants.

## Optional Codex automation

You do **not** need Codex, an OpenAI account, or the companion skill for manual planning, editing, journals, artifacts, graph work, backups, or recovery.

If you do use it, review the project scan policy in **Settings** first. Copying an **Ask Codex** prompt sends nothing. Running that prompt asks Codex to inspect, and may send to Codex/OpenAI, the disclosed monitor context and scan-policy-permitted project text. Every result is a proposal for your review; the agent never applies changes itself.

| Ask Codex mode | Use it when you want to… |
| --- | --- |
| **Initialize structure** | Draft the first pipelines and top-level planned tasks for an empty project. |
| **Expand task** | Break one active task into planned descendants or clarify its planning fields. |
| **Reconcile progress** | Compare allowed source evidence with existing tasks and propose recorded progress or evidence links. |
| **Suggest next work** | Draft additional planned work for a project or pipeline without recording progress. |
| **Record update** | Turn one explicit note about one task into a journal entry and optional progress update. |
| **Link artifacts** | Link one or more explicitly supplied artifact locations to one task. |

The dashboard creates a scoped, expiring intent for the selected mode. It binds the project, scope, semantic revision, planning profile, completion permission, and explicit artifact locators, so Codex cannot mint or broaden it. In **Proposals**, inspect the rationale, evidence, risks, and individual operations; then select the dependency-complete set you want and confirm the atomic application. A no-change result is also useful: it records that the allowed evidence was already up to date, insufficient, or ambiguous.

### Install the optional companion skill

The skill is bundled for convenience but is never installed by the browser or by a normal monitor install. Check its status at any time; this command does not install or replace the skill and does not modify enrolled folders:

```bash
research-monitor skill status
```

To opt in, stop the monitor and install the skill in the same remote environment where you run Codex:

```bash
research-monitor stop
research-monitor skill install
research-monitor serve --port 8765
```

Use `research-monitor skill update` only when a skill is already installed and you deliberately want to replace it. The dashboard reports **Current**, **Missing**, **Modified**, **Outdated**, or **Blocked** and shows the appropriate command. The installer refuses to use a `CODEX_HOME` that overlaps an enrolled project or approved artifact root, even with `--force`.

The bundled skill is contractually instructed not to execute project code, modify research files, make project-directed or arbitrary network requests, or read paths outside the approved scan policy. Its OpenAI connection when you deliberately run Codex is separate. Research Monitor enforces what monitor data and proposal operations it accepts, but it cannot technically prevent a separate process already running as the same OS user from using unrelated filesystem or network tools. Treat the scan policy and the same-user threat boundary accordingly.

The `agent context`, `proposal validate`, and `proposal create` commands are stable interfaces for Codex and other integrations. Ordinary users normally use the dashboard and the commands in this README instead.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `monitor already running` | Stop it with `research-monitor stop`, or intentionally replace it with `research-monitor serve --force-restart`. On a remote host, use `research-monitor open --no-open` to mint a recovery URL for the existing server; if you started it in the foreground, Ctrl+C is also valid. |
| Forwarded page times out or stays blank | Check that the server is still running, choose a different unused **remote** port with `research-monitor serve --port PORT`, then forward that same port in VS Code. Do not use VS Code Simple Browser. |
| “Unable to load this view” or authentication warning | Reopen the bare forwarded address in a normal browser tab. If that does not help, run `research-monitor open --no-open` and open its short-lived one-use URL. |
| A folder cannot be enrolled | Use an existing absolute Linux path, not a Windows path. Check `RESEARCH_MONITOR_ALLOWED_ROOTS`, then restart the server after changing it. |
| No project or task is shown | A new project is intentionally empty. Create a pipeline and task manually, or choose **Initialize structure** under Ask Codex after reviewing the scan policy. |
| Optional skill is Missing, Modified, or Blocked | Manual monitoring still works. Run `research-monitor skill status` for the exact next step; do not install it into a research folder. |

## Backups and recovery

Create a verified backup before a wheel upgrade, a migration, or any change you would be unhappy to lose:

```bash
research-monitor backup create
```

The command prints the backup path and validates it with SQLite's integrity check. Keep that path somewhere safe. The monitor also makes a verified backup before migrations and permanent project purges.

Restoring replaces monitor data, so stop the server and use an explicit confirmation only when you intend to recover:

```bash
research-monitor stop
research-monitor backup restore /path/to/monitor-backup.db --confirm
```

Restore validates the candidate's integrity and schema before replacement and normally creates a fresh pre-restore backup. Archiving, trashing, purging, relinking, restoring monitor state, and backups never modify the research folder.

### Rolling back a v0.2 database

This is an advanced compatibility operation, not a normal restore. While v0.2 is still installed, restore a verified pre-0005 backup with the preserving flag, then reinstall v0.1 before restarting:

```bash
research-monitor backup restore /path/to/pre-0005.db --confirm --rollback-to-v0.1
```

Never start v0.1 against a v0.2 database, and do not restart v0.2 after this preserving restore.

## Advanced operation and security

Most users can rely on the defaults. This section is for people changing storage locations, sharing a database between Linux hosts, or auditing the local security model.

### Storage and local security

Default Linux/XDG locations are:

- Database: `$XDG_DATA_HOME/research-monitor/monitor.db`
- Cross-host writer lock: `$XDG_DATA_HOME/research-monitor/writer.lock`
- Configuration: `$XDG_CONFIG_HOME/research-monitor/config.toml`
- Host-local runtime descriptor, CLI token, and application lock: `$XDG_RUNTIME_DIR/research-monitor/`

If an XDG variable is absent, Research Monitor uses an owner-only fallback below the user data directory. Set `RESEARCH_MONITOR_HOME=/path/to/isolated-home` for tests or a temporary monitor; that override deliberately keeps data and runtime state together.

The server binds only to `127.0.0.1`, accepts local hosts and origins, uses SameSite browser sessions and CSRF protection, and has no CORS or CDN dependency. It protects against malicious browser pages and untrusted project content, but not another process already running as the same OS user.

Local artifacts are stored as approved-root UUIDs plus relative paths. Every access repeats realpath and symlink-containment checks. Secret-like paths and unsafe formats are metadata-only; external artifacts accept only HTTP/HTTPS and are never fetched. Approving an artifact root does not allow Codex to read it: it must separately be selected as a readable source in the project scan policy.

### Shared storage and NFS

The monitor is a single-writer SQLite application. It uses rollback-journal `DELETE` mode with `synchronous=FULL`, which avoids WAL shared-memory coordination and is generally friendlier to network filesystems. It does not guarantee durability on every NFS server or mount.

For one database shared across Linux hosts, keep `XDG_RUNTIME_DIR` host-local and place `XDG_DATA_HOME` on the shared filesystem. Only one host may serve or access the monitor offline at a time, and every mount must honor cross-host advisory locks. Host-local `stop` and `--force-restart` never seize an instance on a different host.

The release was crash-, backup-, and restore-tested on an actual NFS filesystem from one host. A true two-host deployment is not certified. Prefer local storage for important monitor state unless you have verified advisory locking and crash durability for your NFS deployment, and keep verified backups.

## Development

This section is for contributors building from source. End users should install a released wheel as described in [Start here](#start-here).

Requirements:

- Python 3.12 and `uv`
- A current Node.js and npm release for frontend work only. Node.js 24 was used for v0.2 validation.

Install and test the backend:

```bash
uv sync --extra dev
uv run pytest tests/backend
uv run pytest tests/skill
```

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
# Set SKILL_CREATOR_ROOT if the official skill-creator checkout is elsewhere.
uv run python "${SKILL_CREATOR_ROOT:-$HOME/.codex/skills/.system/skill-creator}/scripts/quick_validate.py" skills/research-monitor
uv build
```

The backend suite verifies an isolated installed wheel with no Node runtime and no source-tree import bridge. It separately verifies the optional skill stays missing during the core smoke test and can be explicitly installed into an isolated `CODEX_HOME`. The Playwright suite drives the production FastAPI/Vite bundle through the real HTTP, CSRF, persistence, and browser-session boundaries.

The full release process is in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md). The project is licensed under the [MIT License](LICENSE).
