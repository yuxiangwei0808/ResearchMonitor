from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from research_monitor.release_validation import (
    copy_verified_tree,
    tree_manifest,
    validate_frontend_tree,
)
from research_monitor.skill_validation import (
    SKILL_FILES,
    SkillBundleValidationError,
    validate_skill_tree,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SKILL = ROOT / "skills" / "research-monitor"


def copy_skill(tmp_path: Path) -> Path:
    destination = tmp_path / "research-monitor"
    shutil.copytree(SOURCE_SKILL, destination)
    return destination


def test_checked_in_skill_and_frontend_release_trees_are_valid() -> None:
    validate_skill_tree(SOURCE_SKILL)
    validate_frontend_tree(ROOT / "frontend" / "dist")


@pytest.mark.parametrize("relative", sorted(SKILL_FILES))
def test_skill_validator_rejects_each_missing_versioned_file(
    tmp_path: Path, relative: str
) -> None:
    skill = copy_skill(tmp_path)
    (skill / relative).unlink()
    with pytest.raises(SkillBundleValidationError, match="missing"):
        validate_skill_tree(skill)


def test_skill_validator_rejects_extra_files_and_symlinks(tmp_path: Path) -> None:
    skill = copy_skill(tmp_path)
    (skill / "README.md").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(SkillBundleValidationError, match="unexpected"):
        validate_skill_tree(skill)

    (skill / "README.md").unlink()
    (skill / "linked-skill.md").symlink_to(skill / "SKILL.md")
    with pytest.raises(SkillBundleValidationError, match="symlinks"):
        validate_skill_tree(skill)


def test_skill_validator_rejects_malformed_frontmatter_and_agent_metadata(
    tmp_path: Path,
) -> None:
    skill = copy_skill(tmp_path)
    skill_file = skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(
            "name: research-monitor", "name: other-skill"
        ),
        encoding="utf-8",
    )
    with pytest.raises(SkillBundleValidationError, match="name must be research-monitor"):
        validate_skill_tree(skill)

    skill = copy_skill(tmp_path / "second")
    agent = skill / "agents" / "openai.yaml"
    agent.write_text(
        agent.read_text(encoding="utf-8") + '  extra_field: "not allowed"\n',
        encoding="utf-8",
    )
    with pytest.raises(SkillBundleValidationError, match="exactly"):
        validate_skill_tree(skill)


@pytest.mark.parametrize(
    ("relative", "marker"),
    [
        ("references/cli-contract.md", "research-monitor version --json"),
        ("references/change-set-schema.md", "`pipeline.create`"),
    ],
)
def test_skill_validator_rejects_stale_generated_contracts(
    tmp_path: Path, relative: str, marker: str
) -> None:
    skill = copy_skill(tmp_path)
    reference = skill / relative
    reference.write_text(
        reference.read_text(encoding="utf-8").replace(marker, f"{marker}-stale", 1),
        encoding="utf-8",
    )
    with pytest.raises(SkillBundleValidationError, match="stale"):
        validate_skill_tree(skill)


def test_frontend_validator_rejects_missing_index_assets(tmp_path: Path) -> None:
    frontend = tmp_path / "dist"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("export {}\n", encoding="utf-8")
    (assets / "app.css").write_text(":root {}\n", encoding="utf-8")
    (frontend / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>'
        '<link rel="stylesheet" href="/assets/app.css">',
        encoding="utf-8",
    )
    validate_frontend_tree(frontend)
    (assets / "app.js").unlink()
    with pytest.raises(RuntimeError, match="missing assets"):
        validate_frontend_tree(frontend)


def test_verified_copy_removes_stale_destination_assets(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "current.js").write_text("current\n", encoding="utf-8")
    (destination / "stale.js").write_text("stale\n", encoding="utf-8")
    copy_verified_tree(source, destination)
    assert tree_manifest(destination) == tree_manifest(source)
    assert not (destination / "stale.js").exists()
