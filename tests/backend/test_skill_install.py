from __future__ import annotations

import json
import os
import shutil
import stat
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import research_monitor.cli as cli_module
import research_monitor.skill_installation as installer_module
from research_monitor.cli import _install_skill, _tree_hash, app
from research_monitor.config import Settings
from research_monitor.database import get_database, reset_database_singleton
from research_monitor.locking import ApplicationLock
from research_monitor.models import ArtifactRoot, Project, utcnow
from research_monitor.service import DomainError
from research_monitor.skill_installation import (
    SkillManagedPaths,
    install_skill,
    skill_destination_overlap,
    skill_managed_paths,
    skill_status_value,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "skills" / "research-monitor"
runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_monitor_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RESEARCH_MONITOR_HOME", str(tmp_path / "monitor-home"))
    monkeypatch.setenv("RESEARCH_MONITOR_ALLOWED_ROOTS", str(tmp_path))
    reset_database_singleton()
    yield
    reset_database_singleton()


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
    real_copytree = installer_module.shutil.copytree

    def forbid_unvalidated_source_copy(source, *args, **kwargs):
        assert Path(source) != malformed, "malformed source was copied before validation"
        return real_copytree(source, *args, **kwargs)

    monkeypatch.setattr(
        installer_module.shutil, "copytree", forbid_unvalidated_source_copy
    )
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


def _enroll(root: Path) -> str:
    root.mkdir(parents=True)
    result = runner.invoke(
        app,
        ["project", "add", str(root), "--json"],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]["project"]["id"]


@pytest.mark.parametrize("lifecycle", ["active", "archived", "trashed"])
def test_installer_rejects_project_root_in_every_retained_lifecycle(
    tmp_path: Path, monkeypatch, lifecycle: str
) -> None:
    project_root = tmp_path / f"project-{lifecycle}"
    project_id = _enroll(project_root)
    if lifecycle != "active":
        database = get_database(Settings.load())
        with database.write_session() as session:
            project = session.get(Project, project_id)
            assert project is not None
            if lifecycle == "archived":
                project.archived_at = utcnow()
            else:
                project.trashed_at = utcnow()

    monkeypatch.setenv("CODEX_HOME", str(project_root / ".codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    with pytest.raises(DomainError) as error:
        _install_skill(True)
    assert error.value.code == "skill_destination_overlaps_project"
    assert not (project_root / ".codex").exists()


def test_installer_rejects_additional_artifact_root_and_destination_parent(
    tmp_path: Path, monkeypatch
) -> None:
    project_id = _enroll(tmp_path / "ordinary-project")
    codex_home = tmp_path / "codex"
    nested_root = codex_home / "skills" / "research-monitor" / "future-artifacts"
    nested_root.mkdir(parents=True)
    database = get_database(Settings.load())
    with database.write_session() as session:
        project = session.get(Project, project_id)
        assert project is not None
        project.trashed_at = utcnow()
        session.add(
            ArtifactRoot(
                id=str(uuid4()),
                project_id=project_id,
                alias="Additional",
                root_path=str(nested_root.resolve()),
                is_project_root=False,
            )
        )

    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    with pytest.raises(DomainError) as error:
        _install_skill(False)
    assert error.value.code == "skill_destination_overlaps_project"
    assert error.value.details["protected_kind"].startswith("artifact root ")


def test_installer_resolves_codex_home_symlink_before_any_destination_access(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / "symlink-project"
    _enroll(project_root)
    symlink_home = tmp_path / "codex-link"
    symlink_home.symlink_to(project_root / ".codex", target_is_directory=True)
    monkeypatch.setenv("CODEX_HOME", str(symlink_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))

    with pytest.raises(DomainError) as error:
        _install_skill(False)
    assert error.value.code == "skill_destination_overlaps_project"
    assert not (project_root / ".codex").exists()


@pytest.mark.parametrize(
    ("field", "expected_kind"),
    [
        ("destination", "skill destination"),
        ("state", "installer state"),
        ("work", "installer work directory"),
        ("lock", "installer lock"),
        ("staging", "installer staging directory"),
        ("previous", "previous installation"),
        ("backups", "modified-install backups"),
    ],
)
@pytest.mark.parametrize("direction", ["protected_contains", "managed_contains"])
def test_every_managed_installer_path_is_checked_in_both_directions(
    tmp_path: Path,
    field: str,
    expected_kind: str,
    direction: str,
) -> None:
    # Use disjoint synthetic paths so an enclosing work directory cannot hide
    # an omitted check for one of its explicitly managed descendants.
    managed = {
        name: tmp_path / "managed" / name
        for name in (
            "destination",
            "state",
            "work",
            "lock",
            "staging",
            "previous",
            "backups",
        )
    }
    if direction == "protected_contains":
        protected = tmp_path / "protected" / field
        managed[field] = protected / "installer-entry"
    else:
        managed[field] = tmp_path / "installer" / field
        protected = managed[field] / "retained-project"
    paths = SkillManagedPaths(**managed)

    # Guard this test against a future managed-path field being omitted from
    # labelled(), which is the collection consumed by overlap validation.
    assert {label for label, _path in paths.labelled()} == {
        "skill destination",
        "installer state",
        "installer work directory",
        "installer lock",
        "installer staging directory",
        "previous installation",
        "modified-install backups",
    }
    overlap = skill_destination_overlap(paths, (("project test", protected),))
    assert overlap is not None
    assert overlap["managed_kind"] == expected_kind
    assert overlap["protected_kind"] == "project test"


def test_blocked_status_is_passive_and_does_not_read_destination(
    tmp_path: Path, monkeypatch
) -> None:
    protected = tmp_path / "protected"
    destination = protected / ".codex" / "skills" / "research-monitor"
    destination.mkdir(parents=True)
    unreadable = destination / "SKILL.md"
    unreadable.write_text("must not be inspected", encoding="utf-8")
    unreadable.chmod(0)
    monkeypatch.setenv("CODEX_HOME", str(protected / ".codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    try:
        status = skill_status_value((("project test", protected),))
    finally:
        unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert status["optional"] is True
    assert status["normalized_status"] == "blocked"
    assert status["setup_command"] == (
        "CODEX_HOME=/safe/codex-home research-monitor skill install"
    )
    assert status["blocking_reason"]


def test_concurrent_install_returns_structured_busy_error_and_private_lock(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    paths = skill_managed_paths()
    paths.work.mkdir(parents=True, mode=0o700)
    held = ApplicationLock(paths.lock)
    assert held.acquire()
    try:
        with pytest.raises(DomainError) as error:
            install_skill(False, ())
        assert error.value.code == "skill_install_busy"
        assert error.value.details["lock_path"] == str(paths.lock)
        assert stat.S_IMODE(paths.lock.stat().st_mode) == 0o600
        cli_result = runner.invoke(app, ["skill", "install"])
        assert cli_result.exit_code == 6
        cli_error = json.loads(cli_result.output)["error"]
        assert cli_error["code"] == "skill_install_busy"
        assert cli_error["details"]["lock_path"] == str(paths.lock)
    finally:
        held.release()


def test_cli_installer_requires_stopped_monitor_before_destination_write(
    tmp_path: Path, monkeypatch
) -> None:
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    running = ApplicationLock(Settings.load().lock_path)
    assert running.acquire()
    try:
        with pytest.raises(DomainError) as error:
            _install_skill(False)
        assert error.value.code == "application_running"
        assert "Stop Research Monitor" in error.value.message
        assert not (codex_home / "skills" / "research-monitor").exists()
    finally:
        running.release()


def test_api_reports_optional_blocked_status_without_installing(
    client: TestClient, project_root: Path, monkeypatch
) -> None:
    enrolled = client.post(
        "/api/v1/projects",
        json={"name": "Protected", "root_path": str(project_root)},
    )
    assert enrolled.status_code == 201, enrolled.text
    monkeypatch.setenv("CODEX_HOME", str(project_root / ".codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))

    response = client.get("/api/v1/skill-status")
    assert response.status_code == 200, response.text
    status = response.json()
    assert status["optional"] is True
    assert status["status"] == "Blocked"
    assert status["normalized_status"] == "blocked"
    assert status["destination"].startswith(str(project_root))
    assert not (project_root / ".codex").exists()


def test_status_root_discovery_uses_running_server_public_path_contract(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings.load()
    settings.database_path.touch()
    project_root = tmp_path / "server-project"
    artifact_root = tmp_path / "server-artifacts"

    class FakeClient:
        def request(self, _method, path, *, params=None, json_body=None):
            del json_body
            if path == "/api/v1/projects":
                assert params == {"include_archived": True, "include_trashed": True}
                return {
                    "projects": [
                        {"id": "project-id", "root_path": str(project_root)}
                    ]
                }
            assert path == "/api/v1/projects/project-id/snapshot"
            assert params == {"sections": "artifact_roots"}
            return {
                "artifact_roots": [
                    {
                        "id": "artifact-root-id",
                        "canonical_path": str(artifact_root),
                    }
                ]
            }

    monkeypatch.setattr(
        cli_module, "_try_data_access_locks", lambda _settings: (None, "local", {})
    )
    monkeypatch.setattr(cli_module, "_verified_client", lambda _settings: FakeClient())
    roots = cli_module._skill_protected_roots()
    assert roots == (
        ("project project-id", project_root),
        ("artifact root artifact-root-id", artifact_root),
    )


def test_source_root_overlap_is_rejected_before_destination_creation(
    tmp_path: Path, monkeypatch
) -> None:
    protected = tmp_path / "protected-project"
    source = protected / "claimed-skill-source"
    shutil.copytree(SOURCE, source)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source))

    def forbid_source_path_walk(_path):
        pytest.fail("overlapping source was traversed before lexical rejection")

    monkeypatch.setattr(
        installer_module, "_first_symlink_component", forbid_source_path_walk
    )

    with pytest.raises(DomainError) as error:
        install_skill(False, (("project test", protected),))
    assert error.value.code == "skill_source_overlaps_project"
    assert not (tmp_path / "codex").exists()


def test_ordinary_runtime_ignores_ungated_source_override(
    tmp_path: Path, monkeypatch
) -> None:
    decoy = tmp_path / "decoy-source"
    shutil.copytree(SOURCE, decoy)
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(decoy))
    monkeypatch.delenv("RESEARCH_MONITOR_ENABLE_TEST_SKILL_SOURCE")

    assert installer_module.skill_source() == SOURCE.resolve()


def test_installed_layout_prefers_bundled_tree_over_adjacent_tree(
    tmp_path: Path, monkeypatch
) -> None:
    package = (
        tmp_path
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "research_monitor"
    )
    package.mkdir(parents=True)
    bundled = package / "bundled_skill"
    shutil.copytree(SOURCE, bundled)
    adjacent = package.parents[1] / "skills" / "research-monitor"
    shutil.copytree(SOURCE, adjacent)
    with (adjacent / "SKILL.md").open("a", encoding="utf-8") as handle:
        handle.write("\nAdjacent decoy that production must not select.\n")
    monkeypatch.setattr(
        installer_module, "__file__", str(package / "skill_installation.py")
    )
    monkeypatch.delenv("RESEARCH_MONITOR_SKILL_SOURCE", raising=False)

    assert installer_module.skill_source() == bundled.resolve()


@pytest.mark.parametrize("link_kind", ["root", "required_file"])
def test_source_symlinks_are_rejected_without_reading_their_targets(
    tmp_path: Path, monkeypatch, link_kind: str
) -> None:
    protected = tmp_path / "protected-project"
    protected.mkdir()
    secret = protected / "secret.txt"
    secret.write_text("must-not-be-copied", encoding="utf-8")
    ordinary_source = tmp_path / "ordinary-source"
    shutil.copytree(SOURCE, ordinary_source)
    if link_kind == "root":
        source = tmp_path / "source-link"
        source.symlink_to(ordinary_source, target_is_directory=True)
    else:
        source = ordinary_source
        (source / "SKILL.md").unlink()
        (source / "SKILL.md").symlink_to(secret)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source))

    with pytest.raises(DomainError) as error:
        install_skill(False, (("project test", protected),))
    assert error.value.code == "skill_source_unsafe"
    assert not (tmp_path / "codex").exists()
    assert secret.read_text(encoding="utf-8") == "must-not-be-copied"


def test_force_backup_preserves_symlink_without_copying_project_content(
    tmp_path: Path, monkeypatch
) -> None:
    protected = tmp_path / "protected-project"
    protected.mkdir()
    target = protected / "research-result.txt"
    target.write_text("private-result", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = install_skill(False, (("project test", protected),))
    destination = Path(str(installed["path"]))
    link = destination / "user-result-link"
    link.symlink_to(target)

    updated = install_skill(True, (("project test", protected),))
    backup_link = Path(str(updated["backup"])) / link.name
    assert backup_link.is_symlink()
    assert os.readlink(backup_link) == str(target)
    assert target.read_text(encoding="utf-8") == "private-result"


def test_empty_directory_is_a_modification_and_is_preserved_in_forced_backup(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = install_skill(False, ())
    destination = Path(str(installed["path"]))
    (destination / "user-empty-directory").mkdir()

    status = skill_status_value(())
    assert status["normalized_status"] == "modified"
    with pytest.raises(DomainError) as error:
        install_skill(False, ())
    assert error.value.code == "skill_modified"
    updated = install_skill(True, ())
    assert (Path(str(updated["backup"])) / "user-empty-directory").is_dir()


def test_special_node_blocks_status_and_force_update(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = install_skill(False, ())
    fifo = Path(str(installed["path"])) / "unsafe-fifo"
    os.mkfifo(fifo)

    status = skill_status_value(())
    assert status["normalized_status"] == "blocked"
    assert "special filesystem nodes" in str(status["blocking_reason"])
    with pytest.raises(DomainError) as error:
        install_skill(True, ())
    assert error.value.code == "skill_installation_unsafe"


def test_unreadable_installed_tree_returns_blocked_status(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = install_skill(False, ())
    destination = Path(str(installed["path"]))
    real_open = installer_module.os.open

    def deny_installed_file(path, flags, *args, **kwargs):
        candidate = Path(path)
        if candidate == destination / "SKILL.md" and flags & os.O_RDONLY == os.O_RDONLY:
            raise PermissionError("simulated unreadable installed file")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(installer_module.os, "open", deny_installed_file)
    status = skill_status_value(())
    assert status["normalized_status"] == "blocked"
    assert "cannot be inspected safely" in str(status["blocking_reason"])


def test_unreadable_destination_resolution_returns_blocked_status(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))

    def deny_resolution(_destination=None):
        raise PermissionError("simulated unreadable CODEX_HOME")

    monkeypatch.setattr(installer_module, "skill_managed_paths", deny_resolution)
    status = skill_status_value(())
    assert status["normalized_status"] == "blocked"
    assert status["installed"] is False
    assert "cannot be inspected safely" in str(status["blocking_reason"])


def test_interrupted_destination_rename_is_rolled_back_immediately(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = install_skill(False, ())
    destination = Path(str(installed["path"]))
    paths = skill_managed_paths(destination)
    original_hash = _tree_hash(destination)
    real_replace = installer_module.os.replace
    interrupted = False

    def interrupt_after_first_rename(source, target):
        nonlocal interrupted
        result = real_replace(source, target)
        if Path(source) == destination and Path(target) == paths.previous and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("simulated process interruption")
        return result

    monkeypatch.setattr(installer_module.os, "replace", interrupt_after_first_rename)
    with pytest.raises(KeyboardInterrupt):
        install_skill(False, ())
    assert _tree_hash(destination) == original_hash
    assert not paths.previous.exists()


def test_orphaned_previous_tree_is_recovered_on_next_install(
    tmp_path: Path, monkeypatch
) -> None:
    source_v1 = tmp_path / "source-v1"
    source_v2 = tmp_path / "source-v2"
    shutil.copytree(SOURCE, source_v1)
    shutil.copytree(SOURCE, source_v2)
    with (source_v2 / "references" / "cli-contract.md").open("a", encoding="utf-8") as handle:
        handle.write("\nSynthetic v2 marker.\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source_v1))
    installed = install_skill(False, ())
    destination = Path(str(installed["path"]))
    paths = skill_managed_paths(destination)
    os.replace(destination, paths.previous)
    shutil.copytree(source_v2, destination, symlinks=True)
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(source_v2))

    interrupted_status = skill_status_value(())
    assert interrupted_status["normalized_status"] == "blocked"
    assert "interrupted" in str(interrupted_status["blocking_reason"])

    recovered = install_skill(False, ())
    assert _tree_hash(destination) == _tree_hash(source_v2)
    assert not paths.previous.exists()
    preserved = list(paths.backups.glob("interrupted-candidate-*"))
    assert len(preserved) == 1
    assert _tree_hash(preserved[0]) == _tree_hash(source_v2)
    assert recovered["modified_install_replaced"] is False


@pytest.mark.parametrize("tamper", ["root_symlink", "fifo_child"])
def test_unsafe_previous_tree_is_rejected_before_recovery(
    tmp_path: Path, monkeypatch, tamper: str
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("RESEARCH_MONITOR_SKILL_SOURCE", str(SOURCE))
    installed = install_skill(False, ())
    destination = Path(str(installed["path"]))
    paths = skill_managed_paths(destination)
    os.replace(destination, paths.previous)
    if tamper == "root_symlink":
        shutil.rmtree(paths.previous)
        foreign = tmp_path / "foreign-tree"
        foreign.mkdir()
        paths.previous.symlink_to(foreign, target_is_directory=True)
    else:
        os.mkfifo(paths.previous / "unsafe-fifo")

    with pytest.raises(DomainError) as error:
        install_skill(False, ())
    assert error.value.code == "skill_installation_unsafe"
    assert not destination.exists()
    assert paths.previous.exists() or paths.previous.is_symlink()


def test_true_concurrent_cli_coordination_reports_skill_busy(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    entered = threading.Event()
    release = threading.Event()
    results: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def blocking_install(_force, _roots):
        entered.set()
        assert release.wait(timeout=5)
        return {"installed": True}

    monkeypatch.setattr(cli_module, "_install_optional_skill", blocking_install)

    def first_cli_install() -> None:
        try:
            results.append(_install_skill(False))
        except BaseException as exc:  # pragma: no cover - diagnostic guard
            failures.append(exc)

    thread = threading.Thread(target=first_cli_install)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(DomainError) as error:
            _install_skill(False)
        assert error.value.code == "skill_install_busy"
        assert error.value.details["owner"]["purpose"] == "skill_install"
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert failures == []
    assert results == [{"installed": True}]
