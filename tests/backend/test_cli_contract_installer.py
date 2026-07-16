from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

from typer.testing import CliRunner

from research_monitor.cli import app
from research_monitor.database import reset_database_singleton


runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[2]


def _configure_monitor(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    home = tmp_path / "monitor-home"
    project = tmp_path / "research"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(home))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()
    added = runner.invoke(app, ["project", "add", str(project), "--json"])
    assert added.exit_code == 0, added.output
    return project, json.loads(added.output)["data"]["project"]["id"]


def test_cli_proposal_echoes_request_id_and_non_project_404_is_input_error(
    tmp_path: Path, monkeypatch
) -> None:
    _project, project_id = _configure_monitor(tmp_path, monkeypatch)
    request_id = str(uuid4())
    pipeline_id = str(uuid4())
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(
        json.dumps(
            {
                "api_version": "1",
                "schema_version": "1",
                "request_id": request_id,
                "project_id": project_id,
                "base_semantic_revision": 0,
                "summary": "Initialize a documented pipeline",
                "operations": [
                    {
                        "id": str(uuid4()),
                        "type": "pipeline.create",
                        "data": {"id": pipeline_id, "title": "Plan"},
                        "rationale": "PLAN.md defines this workstream",
                        "confidence": 0.9,
                        "evidence": [{"path": "PLAN.md", "anchor": "Plan"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    validated = runner.invoke(
        app,
        ["proposal", "validate", "--project", project_id, "--file", str(proposal_path)],
    )
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.output)["request_id"] == request_id

    missing = runner.invoke(app, ["proposal", "inspect", str(uuid4()), "--json"])
    assert missing.exit_code == 2
    assert json.loads(missing.output)["error"]["code"] == "proposal_not_found"
    reset_database_singleton()


def test_agent_context_exposes_target_entity_and_exact_envelope_contract(
    tmp_path: Path, monkeypatch
) -> None:
    _project, project_id = _configure_monitor(tmp_path, monkeypatch)
    context = runner.invoke(app, ["agent", "context", "--project", project_id, "--json"])
    assert context.exit_code == 0, context.output
    contract = json.loads(context.output)["data"]["proposal_contract"]
    assert contract["operation_schemas"]["task.update"]["entity_id"] == "target_required"
    assert contract["operation_schemas"]["task.create"]["entity_id"] == "client_generated_required"
    properties = contract["proposal_envelope_json_schema"]["properties"]
    assert properties["api_version"]["default"] == "1"
    assert properties["schema_version"]["default"] == "1"
    reset_database_singleton()


def test_invalid_staged_skill_preserves_modified_install_and_force_backs_it_up(
    tmp_path: Path, monkeypatch
) -> None:
    codex_home = tmp_path / "codex"
    source = REPOSITORY / "skills" / "research-monitor"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source))

    installed = runner.invoke(app, ["skill", "install"])
    assert installed.exit_code == 0, installed.output
    destination = codex_home / "skills" / "research-monitor"
    marker = "\nLocal user customization.\n"
    with (destination / "SKILL.md").open("a", encoding="utf-8") as handle:
        handle.write(marker)

    invalid_source = tmp_path / "invalid-skill"
    shutil.copytree(source, invalid_source)
    (invalid_source / "references" / "cli-contract.md").unlink()
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(invalid_source))
    rejected = runner.invoke(app, ["skill", "update", "--force"])
    assert rejected.exit_code == 2
    assert "Local user customization." in (destination / "SKILL.md").read_text(encoding="utf-8")

    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source))
    updated = runner.invoke(app, ["skill", "update", "--force"])
    assert updated.exit_code == 0, updated.output
    result = json.loads(updated.output)["data"]
    backup = Path(result["backup"])
    assert backup.is_dir()
    assert "Local user customization." in (backup / "SKILL.md").read_text(encoding="utf-8")
    assert "Local user customization." not in (destination / "SKILL.md").read_text(encoding="utf-8")
