"""Recorder: a banked discovery run -> Capability. No browser, no LLM, no tokens.

Rebuilds the capability from the committed real discovery evidence (the "run without live
services" path) and asserts the guarantees: it validates, every acting step carries a durable
locator, member_id is bound (not the literal), the output carries its durable locator + a label
anchor, and no secret leaks into the artifact.
"""

from __future__ import annotations

from pathlib import Path

from lyrebird.capability.schema import (
    Capability, ConditionDetector, InputParam, KnownCondition, OutcomeClass,
    OutcomeCode, OutputSpec, Provenance, Target, Viewport,
)
from lyrebird.recorder import record_capability
from lyrebird.recorder.binding import bind_value

ROOT = Path(__file__).resolve().parents[1]
# the latest committed real mock discovery run (127.0.0.1 = local mock) — carries per-element
# durable locators + the LLM's read_value judgment.
EVIDENCE = sorted((ROOT / "evidence").glob("discover-127_0_0_1-*"))[-1]


def _inputs() -> list[InputParam]:
    return [
        InputParam(name="username", type="string", example="teller"),
        InputParam(name="password", type="string", sensitive=True),
        InputParam(name="member_id", type="string", example="100001"),
    ]


def _build() -> Capability:
    return record_capability(
        EVIDENCE / "steps.jsonl",
        EVIDENCE / "aria",
        capability_id="lookup_savings_balance",
        name="Look up member savings balance",
        description="Sign in, search a member by ID, read the savings balance.",
        inputs=_inputs(),
        outputs=[OutputSpec(name="savings_balance", type="number")],
        known_conditions=[
            KnownCondition(code=OutcomeCode.NOT_FOUND, klass=OutcomeClass.BUSINESS_OUTCOME,
                           detector=ConditionDetector(by="text", match="No such member"),
                           on_detect="return_outcome"),
        ],
        success_checkpoint=ConditionDetector(by="text", match="Savings"),
        target=Target(app_id="mock_cu", vendor="lyrebird-mock", entry_url_pattern="/member", tenant="base"),
        provenance=Provenance(discovery_run_id=EVIDENCE.name, model="claude-sonnet-4-6",
                              created_at="2026-09-14T00:00:00Z",
                              viewport=Viewport(width=1280, height=900, device_scale_factor=1.0)),
    )


def test_emitted_capability_validates() -> None:
    cap = _build()
    assert Capability.model_validate_json(cap.model_dump_json()) == cap


def test_each_acting_step_has_a_durable_locator() -> None:
    cap = _build()
    acting = [s for s in cap.steps if s.action in ("type", "click", "select")]
    assert acting, "expected acting steps"
    for s in acting:
        assert s.target is not None and s.target.candidates, f"step {s.index} has no durable locator"


def test_output_has_label_anchored_locator() -> None:
    cap = _build()
    out = cap.outputs[0]
    assert out.locator is not None, "output should carry a durable locator"
    # a variable value is anchored on a stable label, never on the literal value
    kinds = {c.kind for c in out.locator.candidates}
    assert "label" in kinds, f"variable output should have a label anchor, got {kinds}"


def test_member_id_bound_and_no_secret_in_artifact() -> None:
    cap = _build()
    blob = cap.model_dump_json()
    assert any(s.value == "{{member_id}}" for s in cap.steps), "member_id should be a binding"
    assert "{{password}}" in blob and "demo-pass-not-secret" not in blob


# ── binding fallback (value->param matching) ─────────────────────────────────
def test_binding_prefers_explicit() -> None:
    b = bind_value(explicit_binding="{{member_id}}", literal=None, inputs=_inputs())
    assert b and b.value == "{{member_id}}" and b.inferred is False


def test_binding_infers_from_matching_example_and_flags_it() -> None:
    b = bind_value(explicit_binding=None, literal="100001", inputs=_inputs())
    assert b and b.value == "{{member_id}}" and b.inferred is True
