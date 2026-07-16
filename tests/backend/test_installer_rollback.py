from __future__ import annotations

import os
import shutil
from pathlib import Path

from typer.testing import CliRunner

import research_monitor.cli as cli_module
from research_monitor.cli import app


runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[2]


def test_failed_atomic_skill_update_restores_previous_valid_install(
    tmp_path: Path, monkeypatch
) -> None:
    codex_home = tmp_path / "codex"
    source = tmp_path / "bundled-skill"
    shutil.copytree(REPOSITORY / "skills" / "research-monitor", source)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source))
    first = runner.invoke(app, ["skill", "install"])
    assert first.exit_code == 0, first.output

    destination = codex_home / "skills" / "research-monitor"
    old_contract = (destination / "references" / "cli-contract.md").read_text(encoding="utf-8")
    state_path = codex_home / "skills" / ".research-monitor-install.json"
    old_state = state_path.read_text(encoding="utf-8")
    with (source / "references" / "cli-contract.md").open("a", encoding="utf-8") as handle:
        handle.write("\n<!-- simulated newer bundle -->\n")

    real_replace = os.replace

    def fail_state_commit(source_path, destination_path):
        if Path(source_path).name == "install-state.json":
            raise OSError("simulated state commit failure")
        return real_replace(source_path, destination_path)

    monkeypatch.setattr(cli_module.os, "replace", fail_state_commit)
    failed = runner.invoke(app, ["skill", "update"])
    assert failed.exit_code == 2
    assert (destination / "references" / "cli-contract.md").read_text(encoding="utf-8") == old_contract
    assert state_path.read_text(encoding="utf-8") == old_state
