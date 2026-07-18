from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from research_monitor.release_validation import tree_manifest
from research_monitor.skill_validation import SKILL_FILES


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def built_distributions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("release-distributions")
    uv = shutil.which("uv")
    assert uv is not None, "uv is required for release verification"
    result = subprocess.run(
        [uv, "build", "--out-dir", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return output


@pytest.fixture(scope="module")
def built_wheel(built_distributions: Path) -> Path:
    wheels = list(built_distributions.glob("research_monitor-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


@pytest.fixture(scope="module")
def built_sdist(built_distributions: Path) -> Path:
    sdists = list(built_distributions.glob("research_monitor-*.tar.gz"))
    assert len(sdists) == 1
    return sdists[0]


def test_distributions_contain_exact_python_frontend_skill_and_license_trees(
    built_wheel: Path,
    built_sdist: Path,
) -> None:
    expected = (ROOT / "LICENSE").read_bytes()

    with zipfile.ZipFile(built_wheel) as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
        wheel_licenses = [
            name
            for name in archive.namelist()
            if ".dist-info/licenses/" in name and name.endswith("/LICENSE")
        ]
        assert len(wheel_licenses) == 1
        assert archive.read(wheel_licenses[0]) == expected

    with tarfile.open(built_sdist, mode="r:gz") as archive:
        sdist_licenses = [name for name in archive.getnames() if name.endswith("/LICENSE")]
        assert len(sdist_licenses) == 1
        extracted = archive.extractfile(sdist_licenses[0])
        assert extracted is not None
        assert extracted.read() == expected

    skill_prefix = "research_monitor/bundled_skill/"
    skill_files = {name.removeprefix(skill_prefix) for name in names if name.startswith(skill_prefix)}
    assert skill_files == set(SKILL_FILES)

    static_prefix = "research_monitor/static/"
    static_files = {name.removeprefix(static_prefix) for name in names if name.startswith(static_prefix)}
    assert static_files == set(tree_manifest(ROOT / "frontend" / "dist"))

    source_modules = {
        item.relative_to(ROOT / "src").as_posix()
        for item in (ROOT / "src" / "research_monitor").rglob("*.py")
    }
    wheel_modules = {name for name in names if name.startswith("research_monitor/") and name.endswith(".py")}
    assert wheel_modules == source_modules
    assert "research_monitor/preview.py" in wheel_modules


def _run(
    command: list[str],
    *, cwd: Path,
    env: dict[str, str],
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        input=input_text,
        check=False,
    )


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _install_isolated_wheel(
    built_wheel: Path, tmp_path: Path
) -> tuple[Path, Path, Path, dict[str, str]]:
    environment = tmp_path / "venv"
    uv = shutil.which("uv")
    assert uv is not None
    clean_env = os.environ.copy()
    clean_env.pop("PYTHONPATH", None)
    clean_env.pop("PYTHONHOME", None)
    clean_env["PYTHONNOUSERSITE"] = "1"
    created = subprocess.run(
        [uv, "venv", "--python", sys.executable, str(environment)],
        env=clean_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    python = environment / "bin" / "python"
    purelib = Path(
        _run(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            cwd=tmp_path,
            env=clean_env,
        ).stdout.strip()
    )
    installed = _run(
        [uv, "pip", "install", "--python", str(python), str(built_wheel)],
        cwd=tmp_path,
        env=clean_env,
    )
    assert installed.returncode == 0, f"{installed.stdout}\n{installed.stderr}"
    assert not (purelib / "release-test-dependencies.pth").exists()

    runtime_env = clean_env.copy()
    runtime_env.update(
        {
            "VIRTUAL_ENV": str(environment),
            "PATH": f"{environment / 'bin'}:/usr/bin:/bin",
        }
    )
    return python, environment / "bin" / "research-monitor", purelib, runtime_env


def test_installed_wheel_runs_node_free_core_cli_and_server(
    built_wheel: Path, tmp_path: Path
) -> None:
    python, executable, purelib, env = _install_isolated_wheel(built_wheel, tmp_path)

    monitor_home = tmp_path / "monitor-home"
    codex_home = tmp_path / "codex-home"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env.pop("RESEARCH_MONITOR_SKILL_SOURCE", None)
    env.update(
        {
            "RESEARCH_MONITOR_HOME": str(monitor_home),
            "RESEARCH_MONITOR_ALLOWED_ROOTS": str(tmp_path),
            "CODEX_HOME": str(codex_home),
        }
    )
    assert shutil.which("node", path=env["PATH"]) is None

    imported = _run(
        [str(python), "-c", "import research_monitor; print(research_monitor.__file__)"],
        cwd=runtime,
        env=env,
    )
    assert imported.returncode == 0, imported.stderr
    assert Path(imported.stdout.strip()).is_relative_to(purelib)

    version = _run([str(executable), "version", "--json"], cwd=runtime, env=env)
    assert version.returncode == 0, version.stderr
    version_data = json.loads(version.stdout)["data"]
    assert version_data["version"] == "0.2.0"
    assert version_data["api_version"] == "1"
    assert version_data["capabilities"] == {
        "guided_agent_intents": 1, "proposal_contract": 2,
        "scoped_agent_context": 1, "no_change_results": 1,
    }

    skill_status = _run([str(executable), "skill", "status"], cwd=runtime, env=env)
    assert skill_status.returncode == 0, skill_status.stdout
    skill_data = json.loads(skill_status.stdout)["data"]
    assert skill_data["optional"] is True
    assert skill_data["normalized_status"] == "missing"
    assert not (codex_home / "skills" / "research-monitor").exists()

    research_root = tmp_path / "guided-research"
    research_root.mkdir()
    enrolled = _run(
        [str(executable), "project", "add", str(research_root), "--json"],
        cwd=runtime,
        env=env,
    )
    assert enrolled.returncode == 0, f"{enrolled.stdout}\n{enrolled.stderr}"
    project_id = json.loads(enrolled.stdout)["data"]["project"]["id"]

    port = _free_port()
    server = subprocess.Popen(
        [str(executable), "serve", "--port", str(port)],
        cwd=runtime,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        descriptor_path = monitor_home / "server.json"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not descriptor_path.exists():
            if server.poll() is not None:
                break
            time.sleep(0.05)
        assert descriptor_path.exists(), server.stdout.read() if server.stdout else ""
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        cookie_jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )
        response_body: bytes | None = None
        while time.monotonic() < deadline:
            try:
                with opener.open(descriptor["browser_url"], timeout=1) as response:
                    response_body = response.read()
                break
            except OSError:
                time.sleep(0.05)
        assert response_body is not None
        index = response_body.decode("utf-8")
        assert "Research Monitor" in index
        assert "/assets/" in index
        with opener.open(f"http://127.0.0.1:{port}/api/v1/version", timeout=2) as response:
            api_version = json.loads(response.read())
        assert api_version["api_version"] == "1"
        assert api_version["version"] == "0.2.0"
        assert api_version["capabilities"] == version_data["capabilities"]
        assert (monitor_home / "monitor.db").is_file()
        csrf = next(
            cookie.value
            for cookie in cookie_jar
            if cookie.name == "research_monitor_csrf"
        )
        intent_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/v1/projects/{project_id}/agent-prompts",
            data=json.dumps({
                "api_version": "1",
                "schema_version": "1",
                "mode": "initialize_structure",
                "scope_type": "project",
                "scope_id": None,
                "instructions": "Create one minimal installed-wheel smoke-test structure.",
                "allow_completion": False,
                "artifact_locators": [],
            }).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Origin": f"http://127.0.0.1:{port}",
                "X-CSRF-Token": csrf,
            },
        )
        with opener.open(intent_request, timeout=2) as response:
            assert response.status == 201
            intent = json.loads(response.read())

        context = _run(
            [
                str(executable), "agent", "context", "--project", project_id,
                "--intent", intent["intent_id"], "--json",
            ],
            cwd=runtime,
            env=env,
        )
        assert context.returncode == 0, f"{context.stdout}\n{context.stderr}"
        context_data = json.loads(context.stdout)["data"]
        assert context_data["intent"]["bound_request_id"] == intent["proposal_request_id"]

        pipeline_id, task_id = str(uuid4()), str(uuid4())
        evidence = [{
            "kind": "user_instruction",
            "intent_id": intent["intent_id"],
            "summary": "The bound installed-wheel smoke request supports this operation.",
        }]
        proposal_payload = {
            "api_version": "1",
            "schema_version": "1",
            "proposal_contract_version": "2",
            "request_id": intent["proposal_request_id"],
            "project_id": project_id,
            "intent_id": intent["intent_id"],
            "base_semantic_revision": 0,
            "result_kind": "changes",
            "summary": "Installed-wheel guided structure",
            "rationale": "Exercise the packaged guided proposal path.",
            "scan_summary": {
                "files_considered": 0,
                "files_read": 0,
                "text_bytes_read": 0,
                "truncated": False,
                "limitations": ["No project text was needed."],
            },
            "operations": [
                {
                    "id": str(uuid4()), "entity_id": pipeline_id,
                    "type": "pipeline.create",
                    "data": {"id": pipeline_id, "title": "Smoke-test workflow"},
                    "basis": "user_instruction", "rationale": "Create one container.",
                    "confidence": 0.95, "evidence": evidence,
                },
                {
                    "id": str(uuid4()), "entity_id": task_id,
                    "type": "task.create",
                    "data": {
                        "id": task_id, "pipeline_id": pipeline_id,
                        "title": "Review the guided smoke proposal",
                        "status": "planned", "outcome": "not_applicable",
                    },
                    "basis": "user_instruction", "rationale": "Create one planned task.",
                    "confidence": 0.95, "evidence": evidence,
                },
            ],
        }
        proposal_json = json.dumps(proposal_payload)
        for command in ("validate", "create"):
            result = _run(
                [
                    str(executable), "proposal", command, "--project", project_id,
                    "--file", "-",
                ],
                cwd=runtime,
                env=env,
                input_text=proposal_json,
            )
            assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
            if command == "validate":
                assert json.loads(result.stdout)["data"]["valid"] is True
            else:
                created_proposal = json.loads(result.stdout)["data"]
        assert created_proposal["proposal_contract_version"] == "2"
        proposal_id = created_proposal["id"]
        detail = _run(
            [str(executable), "proposal", "inspect", proposal_id, "--json"],
            cwd=runtime,
            env=env,
        )
        assert detail.returncode == 0, f"{detail.stdout}\n{detail.stderr}"
        assert json.loads(detail.stdout)["data"]["id"] == proposal_id

        stopped = _run(
            [str(executable), "stop", "--json"],
            cwd=runtime,
            env=env,
        )
        assert stopped.returncode == 0, f"{stopped.stdout}\n{stopped.stderr}"
        stop_payload = json.loads(stopped.stdout)["data"]
        assert stop_payload["stopped"] is True
        assert server.wait(timeout=10) == 0
        assert not descriptor_path.exists()
        server = subprocess.Popen(
            [str(executable), "serve", "--port", str(port)],
            cwd=runtime,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        restart_deadline = time.monotonic() + 20
        while time.monotonic() < restart_deadline and not descriptor_path.exists():
            if server.poll() is not None:
                break
            time.sleep(0.05)
        assert descriptor_path.exists(), server.stdout.read() if server.stdout else ""
        persisted = _run(
            [str(executable), "proposal", "inspect", proposal_id, "--json"],
            cwd=runtime,
            env=env,
        )
        assert persisted.returncode == 0, f"{persisted.stdout}\n{persisted.stderr}"
        assert json.loads(persisted.stdout)["data"]["id"] == proposal_id
        stopped_again = _run(
            [str(executable), "stop", "--json"], cwd=runtime, env=env,
        )
        assert stopped_again.returncode == 0, stopped_again.stdout
        assert server.wait(timeout=10) == 0
        assert not descriptor_path.exists()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_installed_wheel_explicitly_installs_optional_skill(
    built_wheel: Path, tmp_path: Path
) -> None:
    _python, executable, _purelib, env = _install_isolated_wheel(
        built_wheel, tmp_path
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monitor_home = tmp_path / "monitor-home"
    codex_home = tmp_path / "optional-codex-home"
    env.pop("RESEARCH_MONITOR_SKILL_SOURCE", None)
    env.update(
        {
            "RESEARCH_MONITOR_HOME": str(monitor_home),
            "RESEARCH_MONITOR_ALLOWED_ROOTS": str(tmp_path),
            "CODEX_HOME": str(codex_home),
        }
    )

    before = _run([str(executable), "skill", "status"], cwd=runtime, env=env)
    assert before.returncode == 0, before.stdout
    assert json.loads(before.stdout)["data"]["normalized_status"] == "missing"

    installed = _run([str(executable), "skill", "install"], cwd=runtime, env=env)
    assert installed.returncode == 0, installed.stdout
    result = json.loads(installed.stdout)["data"]
    assert result["optional"] is True
    installed_skill = codex_home / "skills" / "research-monitor"
    assert {
        item.relative_to(installed_skill).as_posix()
        for item in installed_skill.rglob("*")
        if item.is_file()
    } == set(SKILL_FILES)

    after = _run([str(executable), "skill", "status"], cwd=runtime, env=env)
    assert after.returncode == 0, after.stdout
    status = json.loads(after.stdout)["data"]
    assert status["normalized_status"] == "current"
    assert status["modified"] is False
