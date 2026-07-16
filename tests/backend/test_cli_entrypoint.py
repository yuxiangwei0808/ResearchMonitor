from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "arguments, expected_fragment",
    [
        (["project", "resolve", "--json"], "Missing option '--path'"),
        (["project", "list", "--not-an-option"], "No such option"),
    ],
)
def test_cli_entrypoint_wraps_parser_errors_in_common_json(
    tmp_path: Path,
    arguments: list[str],
    expected_fragment: str,
) -> None:
    environment = dict(os.environ)
    environment["RESEARCH_MONITOR_HOME"] = str(tmp_path / "monitor-home")
    result = subprocess.run(
        [sys.executable, "-m", "research_monitor.cli", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["api_version"] == "1"
    assert payload["schema_version"] == "1"
    assert payload["request_id"]
    assert payload["error"]["code"] == "invalid_input"
    assert expected_fragment in payload["error"]["message"]


def test_cli_entrypoint_preserves_normal_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "research_monitor.cli", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Local research project task monitor" in result.stdout
    assert not result.stdout.lstrip().startswith("{")
