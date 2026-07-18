"""Strict, dependency-free validation for the versioned companion skill bundle."""

from __future__ import annotations

import json
import importlib.util
import re
from pathlib import Path

try:
    from .contracts import (
        render_cli_reference_block,
        render_evidence_reference_block,
        render_guided_proposal_reference_block,
        render_operation_reference_block,
    )
except ImportError:  # setuptools may load the cmdclass module outside its package.
    _contracts_spec = importlib.util.spec_from_file_location(
        "_research_monitor_build_contracts", Path(__file__).with_name("contracts.py")
    )
    if _contracts_spec is None or _contracts_spec.loader is None:
        raise
    _contracts = importlib.util.module_from_spec(_contracts_spec)
    _contracts_spec.loader.exec_module(_contracts)
    render_cli_reference_block = _contracts.render_cli_reference_block
    render_evidence_reference_block = _contracts.render_evidence_reference_block
    render_guided_proposal_reference_block = (
        _contracts.render_guided_proposal_reference_block
    )
    render_operation_reference_block = _contracts.render_operation_reference_block


SKILL_FILES = frozenset(
    {
        "SKILL.md",
        "agents/openai.yaml",
        "references/cli-contract.md",
        "references/change-set-schema.md",
    }
)
SKILL_DIRECTORIES = frozenset({"agents", "references"})
_KEY = re.compile(r"^[a-z][a-z0-9_-]*$")


class SkillBundleValidationError(ValueError):
    """Raised when a skill tree cannot be safely packaged or installed."""


def _fail(message: str) -> None:
    raise SkillBundleValidationError(message)


def _read_text(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SkillBundleValidationError(f"{path.name} must be readable UTF-8 text") from exc
    if not value.strip() or "\x00" in value:
        _fail(f"{path.name} must be non-empty UTF-8 text without NUL bytes")
    return value


def _plain_scalar(value: str, *, context: str) -> str:
    value = value.strip()
    if not value or value[0] in "|>!&*{}[]":
        _fail(f"{context} must be a non-empty scalar string")
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SkillBundleValidationError(f"{context} has invalid quoted YAML") from exc
        if not isinstance(parsed, str):
            _fail(f"{context} must be a string")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            _fail(f"{context} has invalid quoted YAML")
        return value[1:-1].replace("''", "'")
    if value.endswith(("'", '"')) or " #" in value:
        _fail(f"{context} has ambiguous YAML scalar syntax")
    return value


def _parse_frontmatter(skill_text: str) -> dict[str, str]:
    lines = skill_text.splitlines()
    if not lines or lines[0] != "---":
        _fail("SKILL.md requires leading YAML frontmatter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise SkillBundleValidationError("SKILL.md frontmatter is not closed") from exc
    if closing == 1:
        _fail("SKILL.md frontmatter cannot be empty")
    metadata: dict[str, str] = {}
    for number, raw in enumerate(lines[1:closing], start=2):
        if raw != raw.lstrip() or ":" not in raw:
            _fail(f"SKILL.md frontmatter line {number} must be a flat key/value field")
        key, raw_value = raw.split(":", 1)
        key = key.strip()
        if not _KEY.fullmatch(key) or key in metadata:
            _fail(f"SKILL.md frontmatter has an invalid or duplicate key: {key!r}")
        metadata[key] = _plain_scalar(raw_value, context=f"SKILL.md frontmatter {key!r}")
    if set(metadata) != {"name", "description"}:
        _fail("SKILL.md frontmatter must contain only name and description")
    if metadata["name"] != "research-monitor":
        _fail("SKILL.md frontmatter name must be research-monitor")
    if not metadata["description"].strip():
        _fail("SKILL.md frontmatter description cannot be empty")
    if not "\n".join(lines[closing + 1 :]).strip():
        _fail("SKILL.md body cannot be empty")
    if len(lines) >= 500:
        _fail("SKILL.md must remain under 500 lines")
    return metadata


def _parse_openai_metadata(agent_text: str) -> dict[str, str]:
    lines = [(number, raw) for number, raw in enumerate(agent_text.splitlines(), start=1) if raw.strip()]
    if not lines or lines[0][1] != "interface:":
        _fail("agents/openai.yaml must contain one top-level interface mapping")
    metadata: dict[str, str] = {}
    for number, raw in lines[1:]:
        if "\t" in raw or not raw.startswith("  ") or raw.startswith("   ") or ":" not in raw:
            _fail(f"agents/openai.yaml line {number} must be a two-space-indented key/value field")
        key, raw_value = raw[2:].split(":", 1)
        key = key.strip()
        if not _KEY.fullmatch(key) or key in metadata:
            _fail(f"agents/openai.yaml has an invalid or duplicate key: {key!r}")
        raw_value = raw_value.strip()
        if not raw_value.startswith('"'):
            _fail(f"agents/openai.yaml field {key!r} must use a quoted scalar")
        metadata[key] = _plain_scalar(raw_value, context=f"agents/openai.yaml field {key!r}")
    expected = {"display_name", "short_description", "default_prompt"}
    if set(metadata) != expected:
        _fail("agents/openai.yaml interface must contain exactly display_name, short_description, and default_prompt")
    if metadata["display_name"] != "Research Monitor":
        _fail("agents/openai.yaml display_name must be Research Monitor")
    if not metadata["short_description"].strip():
        _fail("agents/openai.yaml short_description cannot be empty")
    if "$research-monitor" not in metadata["default_prompt"]:
        _fail("agents/openai.yaml default_prompt must invoke $research-monitor")
    return metadata


def _validate_generated_block(text: str, name: str, expected: str, filename: str) -> None:
    start = f"<!-- BEGIN GENERATED: {name} -->"
    end = f"<!-- END GENERATED: {name} -->"
    if text.count(start) != 1 or text.count(end) != 1:
        _fail(f"{filename} must contain exactly one generated {name} block")
    prefix, remainder = text.split(start, 1)
    actual, suffix = remainder.split(end, 1)
    del prefix, suffix
    if actual != f"\n{expected}\n":
        _fail(f"{filename} generated {name} block is stale; regenerate skill contracts")


def validate_skill_tree(path: Path) -> None:
    """Validate exact files, YAML metadata, and backend-generated references."""

    if not path.is_dir() or path.is_symlink():
        _fail("Bundled skill root must be a real directory")
    entries = list(path.rglob("*"))
    symlinks = sorted(item.relative_to(path).as_posix() for item in entries if item.is_symlink())
    if symlinks:
        _fail(f"Skill package cannot contain symlinks: {', '.join(symlinks)}")
    files = {
        item.relative_to(path).as_posix()
        for item in entries
        if item.is_file()
    }
    directories = {
        item.relative_to(path).as_posix()
        for item in entries
        if item.is_dir()
    }
    if files != SKILL_FILES:
        missing = sorted(SKILL_FILES - files)
        unexpected = sorted(files - SKILL_FILES)
        _fail(f"Skill package files differ from the versioned bundle; missing={missing}, unexpected={unexpected}")
    if directories != SKILL_DIRECTORIES:
        missing = sorted(SKILL_DIRECTORIES - directories)
        unexpected = sorted(directories - SKILL_DIRECTORIES)
        _fail(f"Skill package directories differ from the versioned bundle; missing={missing}, unexpected={unexpected}")

    skill_text = _read_text(path / "SKILL.md")
    agent_text = _read_text(path / "agents" / "openai.yaml")
    cli_text = _read_text(path / "references" / "cli-contract.md")
    changes_text = _read_text(path / "references" / "change-set-schema.md")
    _parse_frontmatter(skill_text)
    _parse_openai_metadata(agent_text)
    _validate_generated_block(
        cli_text,
        "stable-cli-commands",
        render_cli_reference_block(),
        "references/cli-contract.md",
    )
    _validate_generated_block(
        changes_text,
        "agent-operation-schemas",
        render_operation_reference_block(),
        "references/change-set-schema.md",
    )
    _validate_generated_block(
        changes_text,
        "guided-proposal-contract",
        render_guided_proposal_reference_block(),
        "references/change-set-schema.md",
    )
    _validate_generated_block(
        changes_text,
        "guided-evidence-fields",
        render_evidence_reference_block(),
        "references/change-set-schema.md",
    )
