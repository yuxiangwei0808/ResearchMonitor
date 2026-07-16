from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "research-monitor"


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    raw = text.split("---\n", 2)[1]
    result: dict[str, str] = {}
    for line in raw.splitlines():
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def test_skill_has_only_required_package_files() -> None:
    files = {
        path.relative_to(SKILL).as_posix()
        for path in SKILL.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert files == {
        "SKILL.md",
        "agents/openai.yaml",
        "references/cli-contract.md",
        "references/change-set-schema.md",
    }


def test_skill_frontmatter_is_trigger_complete_and_minimal() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    metadata = _frontmatter(text)
    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "research-monitor"
    for trigger in ("initialize", "reconcile", "record progress", "W&B", "MLflow"):
        assert trigger.lower() in metadata["description"].lower()
    assert "TODO" not in text
    assert len(text.splitlines()) < 500


def test_openai_metadata_matches_skill_contract() -> None:
    text = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert 'display_name: "Research Monitor"' in text
    match = re.search(r'short_description: "([^"]+)"', text)
    assert match and 25 <= len(match.group(1)) <= 64
    assert 'default_prompt: "Use $research-monitor ' in text
    assert "icon_" not in text
    assert "brand_color" not in text


def test_skill_enforces_review_only_and_read_only_workflow() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8").lower()
    required_guards = (
        "never modify any enrolled project file",
        "never execute project code",
        "never make network requests",
        "never apply or accept the proposal",
        "never merge by title alone",
        "do not create an empty proposal",
        "unchanged repeated scan produces no duplicate proposal",
    )
    for guard in required_guards:
        assert guard in text
    assert "proposal validate" in text
    assert "proposal create" in text


def test_skill_initialization_builds_editable_hierarchy_and_curated_artifacts() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for requirement in (
        "design a monitor rather than transcribing a file list",
        "include at least one meaningful parent/child relationship",
        "Prefer two to eight children under a parent",
        "Avoid one-child nesting",
        "Curate a small set of high-value artifacts",
        "Perform an explicit artifact pass before validation",
        "reserve 0.95 or above for directly stated facts",
        "synthesized by you must stay below 0.90",
        "Never invent a fingerprint",
    ):
        assert requirement in text


def test_git_metadata_commands_cannot_take_optional_locks_or_run_helpers() -> None:
    skill_text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    cli_text = (SKILL / "references" / "cli-contract.md").read_text(
        encoding="utf-8"
    )
    common_prefix = (
        "git --no-optional-locks --no-pager "
        "-c core.fsmonitor=false -c core.hooksPath=/dev/null -C "
    )

    assert "Even a plain status command may otherwise refresh and lock the index" in skill_text
    assert "Do not run any other Git subcommand" in cli_text

    command_examples: list[str] = []
    for document in (skill_text, cli_text):
        command_examples.extend(re.findall(r"`(git [^`\n]+)`", document))
        command_examples.extend(
            line.strip()
            for line in document.splitlines()
            if line.strip().startswith("git ")
        )

    assert len(command_examples) >= 6
    assert all(command.startswith(common_prefix) for command in command_examples)

    allowlisted = [
        line.strip()
        for line in cli_text.splitlines()
        if line.strip().startswith("git ")
    ]
    assert len(allowlisted) == 5
    for command in allowlisted:
        assert "--no-optional-locks" in command
        assert "--no-pager" in command
        assert "core.fsmonitor=false" in command
        assert "core.hooksPath=/dev/null" in command
    for command in (item for item in allowlisted if " diff " in item):
        assert "--no-ext-diff" in command
        assert "--no-textconv" in command
        assert "--ignore-submodules=all" in command


def test_skill_resolves_and_refreshes_context_before_submitting() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    resolve = text.index("research-monitor project resolve")
    context = text.index("research-monitor agent context")
    inspect = text.index("## Inspect safely")
    validate = text.index("research-monitor proposal validate")
    create = text.index("research-monitor proposal create")
    assert resolve < context < inspect < validate < create
    assert "Apply the returned scan policy as a hard upper bound" in text
    assert "Never follow symlinks" in text
    assert "open_proposal_drafts" in text
    assert "do not propose a duplicate" in text.lower()
    assert "proposal apply" not in text
    assert "/mutations" not in text


def test_cli_reference_documents_stable_agent_commands() -> None:
    text = (SKILL / "references" / "cli-contract.md").read_text(encoding="utf-8")
    commands = (
        "version --json",
        "project list --json",
        "project resolve --path PATH --json",
        "agent context --project UUID --json",
        "proposal validate --project UUID --file FILE_OR_-",
        "proposal create --project UUID --file FILE_OR_-",
        "proposal inspect PROPOSAL_ID --json",
        "export project --project UUID",
        "backup create",
        "backup restore PATH --confirm",
    )
    for command in commands:
        assert f"research-monitor {command}" in text
    for code in (0, 2, 3, 4, 5, 6):
        assert f"| {code} |" in text


def test_change_set_reference_keeps_privileged_operations_out_of_skill() -> None:
    text = (SKILL / "references" / "change-set-schema.md").read_text(
        encoding="utf-8"
    )
    for field in (
        '"api_version"',
        '"schema_version"',
        '"request_id"',
        '"base_semantic_revision"',
        '"operations"',
        '"expected_version"',
        '"prerequisite_operation_ids"',
        '"evidence"',
        '"source_references"',
    ):
        assert field in text
    assert "must not propose `project.*`" in text
    assert "`scan_policy.*`" in text
    assert "`artifact_root.*`" in text
    assert "`layout.*`" in text
    assert "human proposal review remains mandatory" in text.lower()
    completion_example = text.split("## Completion example", 1)[1]
    assert '"kind": "completion_text"' in completion_example
    assert '"kind": "source_text"' not in completion_example
