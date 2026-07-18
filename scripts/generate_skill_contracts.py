"""Regenerate backend-owned contract blocks in the bundled skill references."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_monitor.contracts import (  # noqa: E402
    render_cli_reference_block,
    render_evidence_reference_block,
    render_guided_proposal_reference_block,
    render_operation_reference_block,
)


def replace_block(path: Path, name: str, body: str, *, check: bool) -> bool:
    start = f"<!-- BEGIN GENERATED: {name} -->"
    end = f"<!-- END GENERATED: {name} -->"
    text = path.read_text(encoding="utf-8")
    if start not in text or end not in text:
        raise RuntimeError(f"missing generated markers in {path}: {name}")
    prefix, remainder = text.split(start, 1)
    _old, suffix = remainder.split(end, 1)
    rendered = f"{prefix}{start}\n{body}\n{end}{suffix}"
    if check:
        return rendered == text
    path.write_text(rendered, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    references = ROOT / "skills" / "research-monitor" / "references"
    results = [
        replace_block(
            references / "cli-contract.md", "stable-cli-commands",
            render_cli_reference_block(), check=arguments.check,
        ),
        replace_block(
            references / "change-set-schema.md", "agent-operation-schemas",
            render_operation_reference_block(), check=arguments.check,
        ),
        replace_block(
            references / "change-set-schema.md", "guided-proposal-contract",
            render_guided_proposal_reference_block(), check=arguments.check,
        ),
        replace_block(
            references / "change-set-schema.md", "guided-evidence-fields",
            render_evidence_reference_block(), check=arguments.check,
        ),
    ]
    if arguments.check and not all(results):
        print("Generated skill contract references are stale", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
