from __future__ import annotations

import shutil
from pathlib import Path

from typer.testing import CliRunner

from research_monitor.cli import app


runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[2]


def test_unmodified_previous_skill_can_update_without_force(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / "codex"
    source = tmp_path / "bundled-skill"
    shutil.copytree(REPOSITORY / "skills" / "research-monitor", source)
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(tmp_path / "monitor-home"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source))

    first = runner.invoke(app, ["skill", "install"])
    assert first.exit_code == 0, first.output

    # Simulate a newer application bundle. The installed copy still exactly
    # matches the bundle hash recorded at installation time, so this is not a
    # user modification and a routine update must not require --force.
    with (source / "references" / "cli-contract.md").open("a", encoding="utf-8") as handle:
        handle.write("\n<!-- newer bundled contract -->\n")
    updated = runner.invoke(app, ["skill", "update"])
    assert updated.exit_code == 0, updated.output
    assert "newer bundled contract" in (
        codex_home / "skills" / "research-monitor" / "references" / "cli-contract.md"
    ).read_text(encoding="utf-8")
