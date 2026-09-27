"""The Capability artifact schema. Pure data, no browser, no LLM.

Pins the load-bearing contract (R2, evaluation criterion #1): the artifact round-trips
losslessly, exports a valid JSON Schema, never stores a bound param's literal value, and keeps
sensitive examples out. Locators are DurableLocator candidates captured off the DOM node.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lyrebird.capability.schema import (
    Capability,
    ConditionDetector,
    DurableLocator,
    InputParam,
    KnownCondition,
    LocatorCandidate,
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
    balance_locator = DurableLocator(
        semantic_id="savings_balance_value",
        candidates=[
            LocatorCandidate(kind="label", value="Savings", frame="workspace/member"),
            LocatorCandidate(kind="nth", value="td@4", frame="workspace/member"),
        ],
    )
    search_input = DurableLocator(
        semantic_id="member_search_input",
        candidates=[LocatorCandidate(kind="css", value='input[name="q"]')],
    )
    return Capability(
        capability_id="lookup_savings_balance",
        name="Look up member savings balance",
        description="Search a member by ID and read their savings balance.",
        target=Target(app_id="mock_cu", vendor="lyrebird-mock", entry_url_pattern="/member", tenant="base"),
        inputs=[InputParam(name="member_id", type="string", sensitive=False, example="100001")],
        outputs=[OutputSpec(name="savings_balance", type="number", locator=balance_locator, value_seen="$4,210.75")],
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
    assert Capability.model_validate_json(cap.model_dump_json()) == cap


def test_json_schema_exports_and_is_object() -> None:
    schema = export_schema()
    assert isinstance(schema, dict)
    json.dumps(schema)  # must be JSON-serializable
    assert schema.get("type") == "object"
    for field in ("capability_id", "inputs", "outputs", "steps", "success_checkpoint", "provenance"):
        assert field in schema["properties"]


def test_bound_step_never_stores_literal_value() -> None:
    cap = _sample_capability()
    blob = cap.model_dump_json()
    assert "{{member_id}}" in blob                                   # the binding is stored
    assert "100001" not in blob.replace('"example":"100001"', "")   # the literal is NOT (except as the declared example)


def test_sensitive_param_may_not_carry_an_example() -> None:
    # A7 leak guard: a sensitive param's example would be persisted in the artifact.
    with pytest.raises(ValidationError):
        InputParam(name="password", type="string", sensitive=True, example="hunter2")
    InputParam(name="member_id", type="string", sensitive=False, example="100001")  # fine
    InputParam(name="password", type="string", sensitive=True)                       # fine


def test_status_and_outcome_are_typed() -> None:
    r = ReplayResult(status="BUSINESS_OUTCOME", outcome_code=OutcomeCode.NOT_FOUND, evidence_dir="evidence/x")
    assert r.status == "BUSINESS_OUTCOME"
    assert r.outcome_code is OutcomeCode.NOT_FOUND
    with pytest.raises(ValidationError):
        ReplayResult(status="MAYBE", evidence_dir="evidence/x")  # type: ignore[arg-type]


def test_store_save_and_load_roundtrip(tmp_path) -> None:
    store = CapabilityStore(tmp_path)
    cap = _sample_capability()
    assert store.save(cap).exists()
    assert store.load(cap.capability_id, cap.version) == cap
