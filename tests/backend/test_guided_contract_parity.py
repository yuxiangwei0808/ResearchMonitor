from __future__ import annotations

from research_monitor.contracts import (
    GUIDED_EVIDENCE_FIELDS,
    GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES,
    GUIDED_EVIDENCE_REQUIRED_FIELDS,
    render_evidence_reference_block,
)
from research_monitor.guided import _v2_contract_schemas


def test_guided_evidence_validation_schema_and_reference_share_one_contract() -> None:
    operation_schema, envelope_schema = _v2_contract_schemas()
    operation_variants = operation_schema["properties"]["evidence"]["items"]["oneOf"]
    envelope_variants = envelope_schema["properties"]["evidence"]["items"]["oneOf"]
    assert operation_variants == envelope_variants

    variants = {
        variant["properties"]["kind"]["const"]: variant
        for variant in operation_variants
    }
    assert set(variants) == set(GUIDED_EVIDENCE_FIELDS)
    for kind, fields in GUIDED_EVIDENCE_FIELDS.items():
        variant = variants[kind]
        assert set(variant["properties"]) == set(fields)
        assert set(variant["required"]) == set(
            GUIDED_EVIDENCE_REQUIRED_FIELDS[kind]
        )
        expected_alternatives = GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES.get(kind)
        if expected_alternatives:
            assert {
                frozenset(option["required"]) for option in variant["anyOf"]
            } == set(expected_alternatives)
        else:
            assert "anyOf" not in variant

    rendered = render_evidence_reference_block()
    for kind, required in GUIDED_EVIDENCE_REQUIRED_FIELDS.items():
        row = next(line for line in rendered.splitlines() if f"`{kind}`" in line)
        for field in required - {"kind"}:
            assert f"`{field}`" in row
        for alternative in GUIDED_EVIDENCE_IDENTITY_ALTERNATIVES.get(kind, ()):
            for field in alternative:
                assert f"`{field}`" in row
