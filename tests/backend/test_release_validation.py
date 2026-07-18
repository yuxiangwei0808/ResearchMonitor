from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path

import pytest

from research_monitor.release_validation import (
    copy_verified_tree,
    rewrite_reproducible_sdist,
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


def test_sdist_rewrite_canonicalizes_tar_and_gzip_metadata(tmp_path: Path) -> None:
    archives = [tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"]
    for index, archive_path in enumerate(archives):
        with tarfile.open(archive_path, mode="w:gz") as archive:
            directory = tarfile.TarInfo("research_monitor-0.2.0")
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o2700
            directory.mtime = 100 + index
            directory.uid = 1000 + index
            directory.gid = 2000 + index
            directory.uname = f"builder-{index}"
            directory.gname = f"group-{index}"
            archive.addfile(directory)
            payload = b"stable source payload\n"
            member = tarfile.TarInfo("research_monitor-0.2.0/README.md")
            member.mode = 0o600
            member.mtime = 300 + index
            member.uid = 1000 + index
            member.gid = 2000 + index
            member.uname = f"builder-{index}"
            member.gname = f"group-{index}"
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))

    epoch = 1_767_225_600
    for archive_path in archives:
        rewrite_reproducible_sdist(archive_path, epoch)

    assert archives[0].read_bytes() == archives[1].read_bytes()
    with tarfile.open(archives[0], mode="r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(member.name for member in members)
        assert all(member.mtime == epoch for member in members)
        assert all(member.uid == member.gid == 0 for member in members)
        assert all(member.uname == member.gname == "" for member in members)
        assert members[0].mode == 0o755
        assert members[1].mode == 0o644
        assert archive.extractfile(members[1]).read() == b"stable source payload\n"


@pytest.mark.parametrize(("name", "entry_type"), [("../escape", tarfile.REGTYPE), ("linked", tarfile.SYMTYPE)])
def test_sdist_rewrite_rejects_unsafe_or_linked_members(
    tmp_path: Path, name: str, entry_type: bytes
) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.type = entry_type
        member.linkname = "outside" if entry_type == tarfile.SYMTYPE else ""
        archive.addfile(member, io.BytesIO(b"") if entry_type == tarfile.REGTYPE else None)
    with pytest.raises(RuntimeError, match="unsafe archive path|unsupported special entry"):
        rewrite_reproducible_sdist(archive_path, 1_767_225_600)
