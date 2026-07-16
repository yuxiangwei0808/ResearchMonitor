from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

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


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_installed_wheel_runs_node_free_cli_skill_and_server(
    built_wheel: Path, tmp_path: Path
) -> None:
    environment = tmp_path / "venv"
    uv = shutil.which("uv")
    assert uv is not None
    created = subprocess.run(
        [uv, "venv", "--python", sys.executable, str(environment)],
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
            env=os.environ.copy(),
        ).stdout.strip()
    )
    dependency_site = Path(sysconfig.get_paths()["purelib"])
    (purelib / "release-test-dependencies.pth").write_text(
        str(dependency_site) + "\n", encoding="utf-8"
    )
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(built_wheel)],
        cwd=tmp_path,
        env=os.environ.copy(),
    )
    assert installed.returncode == 0, f"{installed.stdout}\n{installed.stderr}"

    monitor_home = tmp_path / "monitor-home"
    codex_home = tmp_path / "codex-home"
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("RESEARCH_MONITOR_SKILL_SOURCE", None)
    env.update(
        {
            "VIRTUAL_ENV": str(environment),
            "PATH": f"{environment / 'bin'}:/usr/bin:/bin",
            "RESEARCH_MONITOR_HOME": str(monitor_home),
            "RESEARCH_MONITOR_ALLOWED_ROOTS": str(tmp_path),
            "CODEX_HOME": str(codex_home),
        }
    )
    assert shutil.which("node", path=env["PATH"]) is None
    executable = environment / "bin" / "research-monitor"

    imported = _run(
        [str(python), "-c", "import research_monitor; print(research_monitor.__file__)"],
        cwd=runtime,
        env=env,
    )
    assert imported.returncode == 0, imported.stderr
    assert Path(imported.stdout.strip()).is_relative_to(purelib)

    version = _run([str(executable), "version", "--json"], cwd=runtime, env=env)
    assert version.returncode == 0, version.stderr
    assert json.loads(version.stdout)["data"]["api_version"] == "1"

    skill_install = _run([str(executable), "skill", "install"], cwd=runtime, env=env)
    assert skill_install.returncode == 0, skill_install.stdout
    installed_skill = codex_home / "skills" / "research-monitor"
    assert {
        item.relative_to(installed_skill).as_posix()
        for item in installed_skill.rglob("*")
        if item.is_file()
    } == set(SKILL_FILES)
    skill_status = _run([str(executable), "skill", "status"], cwd=runtime, env=env)
    assert skill_status.returncode == 0, skill_status.stdout
    assert json.loads(skill_status.stdout)["data"]["modified"] is False

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
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
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
        assert (monitor_home / "monitor.db").is_file()

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
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
