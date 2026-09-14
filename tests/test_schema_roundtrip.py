"""C4a — the Capability artifact schema. Pure data, no browser, no LLM.

These tests pin the load-bearing contract (R2, evaluation criterion #1): the artifact
round-trips losslessly, exports a valid JSON Schema, enforces the surface-agnostic locator
invariant (A12 — no CSS/XPath), and never stores a bound param's literal value.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lyrebird.capability.schema import (
    Capability,
    ConditionDetector,
    InputParam,
    KnownCondition,
    LocatorCandidate,
    LocatorSpec,
    OutcomeClass,
    OutcomeCode,
    OutputSpec,
    Provenance,
    ReplayResult,
    Risk,
    Step,
    Target,
    Viewport,
)
from lyrebird.capability.jsonschema_export import export_schema
from lyrebird.capability.store import CapabilityStore


def _sample_capability() -> Capability:
    """A minimal but complete read-only 'lookup savings balance' capability."""
    balance_locator = LocatorSpec(
        semantic_id="savings_balance_cell",
        candidates=[
            LocatorCandidate(strategy="label_proximity", args={"label": "Savings"}, confidence=0.9),
            LocatorCandidate(strategy="relative_anchor", args={"anchor": "Savings", "relation": "next_cell"}, confidence=0.7),
        ],
    )
    search_input = LocatorSpec(
        semantic_id="member_search_input",
        candidates=[LocatorCandidate(strategy="label_proximity", args={"label": "Member ID"}, confidence=0.9)],
    )
    return Capability(
        capability_id="lookup_savings_balance",
        name="Look up member savings balance",
        description="Search a member by ID and read their savings balance.",
        target=Target(app_id="mock_cu", vendor="lyrebird-mock", entry_url_pattern="/member", tenant="base"),
        inputs=[InputParam(name="member_id", type="string", sensitive=False, example="100001")],
        outputs=[
            OutputSpec(name="savings_balance", type="number", extract=balance_locator, transform="currency")
        ],
        steps=[
            Step(
                index=0,
                action="type",
                target=search_input,
                value="{{member_id}}",  # bound, never the literal
                postcondition=ConditionDetector(by="url_pattern", match="/member/*"),
                risk=Risk.SAFE,
                provenance="model",
            ),
        ],
        known_conditions=[
            KnownCondition(
                code=OutcomeCode.NOT_FOUND,
                klass=OutcomeClass.BUSINESS_OUTCOME,
                detector=ConditionDetector(by="text", match="No such member"),
                on_detect="return_outcome",
            ),
        ],
        success_checkpoint=ConditionDetector(by="text", match="Savings"),
        risk_summary="Read-only; no risky steps.",
        provenance=Provenance(
            discovery_run_id="discover-lookup-20260913",
            model="claude-sonnet-4-6",
            created_at="2026-09-13T00:00:00Z",
            viewport=Viewport(width=1280, height=900, device_scale_factor=1.0),
        ),
    )


def test_roundtrip_is_lossless() -> None:
    cap = _sample_capability()
    dumped = cap.model_dump_json()
    reparsed = Capability.model_validate_json(dumped)
    assert reparsed == cap


def test_json_schema_exports_and_is_object() -> None:
    schema = export_schema()
    # It's a dict, valid JSON, and describes an object with the top-level fields.
    assert isinstance(schema, dict)
    json.dumps(schema)  # must be JSON-serializable
    assert schema.get("type") == "object"
    props = schema["properties"]
    for field in ("capability_id", "inputs", "outputs", "steps", "success_checkpoint", "provenance"):
        assert field in props


def test_bound_step_never_stores_literal_value() -> None:
    cap = _sample_capability()
    blob = cap.model_dump_json()
    assert "{{member_id}}" in blob          # the binding is stored
    assert "100001" not in blob.replace('"example":"100001"', "")  # the literal is NOT (except as the declared example)


def test_locator_validator_rejects_css_and_xpath() -> None:
    # The A12 invariant: strategies must be answerable on web AND desktop-AX. CSS/XPath are not.
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="css", args={"selector": ".c1 td"}, confidence=0.5)
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="xpath", args={"path": "//td[2]"}, confidence=0.5)


def test_locator_validator_accepts_allowlisted_strategies() -> None:
    for strat in ("role_name", "visible_text", "label_proximity", "relative_anchor", "bbox"):
        LocatorCandidate(strategy=strat, args={}, confidence=0.5)  # must not raise


def test_sensitive_param_may_not_carry_an_example() -> None:
    # A7 leak guard: a sensitive param's example would be persisted in the artifact.
    with pytest.raises(ValidationError):
        InputParam(name="password", type="string", sensitive=True, example="hunter2")
    # non-sensitive example is fine; sensitive with no example is fine
    InputParam(name="member_id", type="string", sensitive=False, example="100001")
    InputParam(name="password", type="string", sensitive=True)


def test_status_and_outcome_are_typed() -> None:
    r = ReplayResult(status="BUSINESS_OUTCOME", outcome_code=OutcomeCode.NOT_FOUND, evidence_dir="evidence/x")
    assert r.status == "BUSINESS_OUTCOME"
    assert r.outcome_code is OutcomeCode.NOT_FOUND
    # a bogus status must fail validation
    with pytest.raises(ValidationError):
        ReplayResult(status="MAYBE", evidence_dir="evidence/x")  # type: ignore[arg-type]


def test_store_save_and_load_roundtrip(tmp_path) -> None:
    store = CapabilityStore(tmp_path)
    cap = _sample_capability()
    path = store.save(cap)
    assert path.exists()
    loaded = store.load(cap.capability_id, cap.version)
    assert loaded == cap


def test_store_versions_are_immutable_paths(tmp_path) -> None:
    store = CapabilityStore(tmp_path)
    cap = _sample_capability()
    store.save(cap)
    v2 = cap.model_copy(update={"version": 2})
    store.save(v2)
    # distinct versions live at distinct paths; both retrievable
    assert store.load(cap.capability_id, 1).version == 1
    assert store.load(cap.capability_id, 2).version == 2
