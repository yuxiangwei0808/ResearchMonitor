from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from research_monitor.cli import _install_skill, _tree_hash
from research_monitor.service import DomainError


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "skills" / "research-monitor"


def test_force_update_backs_up_user_modified_skill(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = _install_skill(False)
    destination = Path(installed["path"])
    marker = "\n<!-- user customization -->\n"
    with (destination / "SKILL.md").open("a", encoding="utf-8") as handle:
        handle.write(marker)
    with pytest.raises(DomainError, match="local modifications"):
        _install_skill(False)
    updated = _install_skill(True)
    backup = Path(str(updated["backup"]))
    assert backup.is_dir()
    assert marker.strip() in (backup / "SKILL.md").read_text(encoding="utf-8")
    assert _tree_hash(destination) == _tree_hash(SOURCE)


def test_invalid_staged_skill_cannot_replace_valid_install(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = _install_skill(False)
    destination = Path(installed["path"])
    original_hash = _tree_hash(destination)
    malformed = tmp_path / "malformed-skill"
    shutil.copytree(SOURCE, malformed)
    (malformed / "agents" / "openai.yaml").unlink()
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(malformed))
    with pytest.raises(DomainError) as error:
        _install_skill(True)
    assert error.value.code == "skill_validation_failed"
    assert _tree_hash(destination) == original_hash


def test_unmodified_older_skill_updates_without_force(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / "codex"
    source_v1 = tmp_path / "source-v1"; source_v2 = tmp_path / "source-v2"
    shutil.copytree(SOURCE, source_v1); shutil.copytree(SOURCE, source_v2)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source_v1))
    installed = _install_skill(False)
    destination = Path(installed["path"])
    with (source_v2 / "references" / "cli-contract.md").open("a", encoding="utf-8") as handle:
        handle.write("\nVersion-two reference note.\n")
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source_v2))
    updated = _install_skill(False)
    assert updated["backup"] is None
    assert updated["modified_install_replaced"] is False
    assert _tree_hash(destination) == _tree_hash(source_v2)
