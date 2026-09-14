"""C4b — recorder: banked trajectory -> Capability. No browser, no LLM, no tokens.

Regenerates the artifact from the committed discovery evidence (the "run without live
services" path) and asserts the R2/R4/A13/A14 guarantees: validates, ordered candidates per
step, member_id bound not literal, viewport in provenance, and no transcript / no sensitive
value in the artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    Target,
    Viewport,
)
from lyrebird.recorder import record_capability
from lyrebird.recorder.binding import bind_value

ROOT = Path(__file__).resolve().parents[1]
# The committed real discovery run (its ARIA snapshots feed candidate synthesis).
EVIDENCE = next((ROOT / "evidence").glob("discover-lookup-*"))
TRAJ = ROOT / "tests" / "fixtures" / "discovery_trajectory.jsonl"
ARIA = EVIDENCE / "aria"


def _inputs() -> list[InputParam]:
    return [
        InputParam(name="username", type="string", example="teller"),
        InputParam(name="password", type="string", sensitive=True),
        InputParam(name="member_id", type="string", example="100001"),
    ]


def _outputs() -> list[OutputSpec]:
    return [
        OutputSpec(
            name="savings_balance",
            type="number",
            extract=LocatorSpec(
                semantic_id="savings_balance_cell",
                candidates=[LocatorCandidate(strategy="label_proximity", args={"label": "Savings"}, confidence=0.9)],
            ),
            transform="currency",
        )
    ]


def _build() -> Capability:
    return record_capability(
        TRAJ,
        ARIA,
        capability_id="lookup_savings_balance",
        name="Look up member savings balance",
        description="Sign in, search a member by ID, read the savings balance.",
        inputs=_inputs(),
        outputs=_outputs(),
        known_conditions=[
            KnownCondition(
                code=OutcomeCode.NOT_FOUND,
                klass=OutcomeClass.BUSINESS_OUTCOME,
                detector=ConditionDetector(by="text", match="No such member"),
                on_detect="return_outcome",
            )
        ],
        success_checkpoint=ConditionDetector(by="text", match="Savings"),
        target=Target(app_id="mock_cu", vendor="lyrebird-mock", entry_url_pattern="/member", tenant="base"),
        provenance=Provenance(
            discovery_run_id=EVIDENCE.name,
            model="claude-sonnet-4-6",
            created_at="2026-09-14T00:00:00Z",
            viewport=Viewport(width=1280, height=900, device_scale_factor=1.0),
        ),
    )


def test_emitted_capability_validates() -> None:
    cap = _build()
    # round-trips through the schema cleanly
    assert Capability.model_validate_json(cap.model_dump_json()) == cap


def test_each_acting_step_has_ordered_candidates() -> None:
    cap = _build()
    acting = [s for s in cap.steps if s.action in ("type", "click", "select")]
    assert acting, "expected acting steps"
    for s in acting:
        assert s.target is not None, f"step {s.index} has no target"
        assert len(s.target.candidates) >= 1
        # confidences are non-increasing => most-robust-first ordering
        confs = [c.confidence for c in s.target.candidates]
        assert confs == sorted(confs, reverse=True), f"step {s.index} candidates not ordered: {confs}"


def test_member_id_is_bound_not_literal() -> None:
    cap = _build()
    typed = [s for s in cap.steps if s.action == "type" and s.value == "{{member_id}}"]
    assert typed, "member_id should be recorded as a binding"
    blob = cap.model_dump_json()
    # the literal example value must not appear as a step value (only allowed as the declared example)
    assert blob.count("100001") == 1, "100001 should appear only once (the declared example)"


def test_viewport_in_provenance() -> None:
    cap = _build()
    assert cap.provenance.viewport.width == 1280
    assert cap.provenance.viewport.device_scale_factor == 1.0


def test_no_transcript_and_no_sensitive_value_in_artifact() -> None:
    cap = _build()
    blob = cap.model_dump_json()
    # no transcript leakage: reasoning/notes/model prose must not be embedded
    for marker in ("reason", "note", "Successfully signed in", "workspace is clearly"):
        assert marker not in blob, f"transcript leaked via {marker!r}"
    # no sensitive value: the password binding is present, its value is not
    assert "{{password}}" in blob
    assert "demo-pass-not-secret" not in blob


# ── binding fallback (value->param matching, flagged inferred) ────────────────
def test_binding_prefers_explicit() -> None:
    b = bind_value(explicit_binding="{{member_id}}", literal=None, inputs=_inputs())
    assert b and b.value == "{{member_id}}" and b.inferred is False


def test_binding_infers_from_matching_example_and_flags_it() -> None:
    b = bind_value(explicit_binding=None, literal="100001", inputs=_inputs())
    assert b and b.value == "{{member_id}}" and b.inferred is True  # flagged for human review


def test_binding_never_matches_sensitive_literal() -> None:
    # even if a literal equals nothing declared, it's kept as-is; sensitive params have no
    # example to match against, so a stray secret literal is never silently bound.
    b = bind_value(explicit_binding=None, literal="some-constant", inputs=_inputs())
    assert b and b.value == "some-constant" and b.inferred is False
