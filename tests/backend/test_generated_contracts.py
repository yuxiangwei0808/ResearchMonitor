from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from research_monitor.contracts import AGENT_OPERATION_SCHEMAS, AGENT_OPERATION_TYPES


ROOT = Path(__file__).resolve().parents[2]


def test_generated_skill_contract_references_are_current() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_skill_contracts.py"), "--check"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_operation_schema_and_allowlist_cannot_drift() -> None:
    assert set(AGENT_OPERATION_SCHEMAS) == set(AGENT_OPERATION_TYPES)
    assert all(contract["data"]["additional_properties"] is False for contract in AGENT_OPERATION_SCHEMAS.values())
