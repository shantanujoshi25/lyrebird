"""Build the mutating 'open sub-account' capability (up to the risky Confirm).

The mutating flow carries the safety + escalation story: open sub-account -> fill the
multi-field form -> review page, where the final Confirm step is classified RISKY and, in
unattended replay, blocks/escalates rather than clicking (A2). Nothing ever submits.

This capability is authored directly (not from a discovery run) because C6b is about the
risky-step GATE, not about re-discovering the flow — the discovery mechanism is already
proven by C3. Run: `uv run python scripts/build_mutating_artifact.py`. No key needed.
"""

from __future__ import annotations

from pathlib import Path

from lyrebird.capability.schema import (
    Capability, ConditionDetector, InputParam, KnownCondition, LocatorCandidate, LocatorSpec,
    OutcomeClass, OutcomeCode, Provenance, Risk, Step, Target, Viewport,
)
from lyrebird.capability.store import CapabilityStore

ROOT = Path(__file__).resolve().parents[1]


def _role_name(role: str, name: str, conf: float = 0.95) -> LocatorSpec:
    return LocatorSpec(semantic_id=f"{name.lower().replace(' ', '_')}_{role}",
                       candidates=[LocatorCandidate(strategy="role_name", args={"role": role, "name": name}, confidence=conf)])


def _label(label: str, role: str, conf: float = 0.8) -> LocatorSpec:
    return LocatorSpec(semantic_id=f"{label.lower().replace(' ', '_')}_{role}",
                       candidates=[LocatorCandidate(strategy="label_proximity", args={"label": label, "role": role}, confidence=conf)])


def _toggle(text: str, role: str, conf: float = 0.85) -> LocatorSpec:
    """Radios/checkboxes carry a state-suffixed accessible name ('Electronic (unchecked)'),
    so match by visible_text (substring of the name) rather than exact role_name."""
    return LocatorSpec(semantic_id=f"{text.lower().replace(' ', '_')}_{role}",
                       candidates=[LocatorCandidate(strategy="visible_text", args={"text": text}, confidence=conf)])


def build() -> Capability:
    return Capability(
        capability_id="open_subaccount",
        name="Open a member sub-account (to review)",
        description="Open the sub-account form for a member, fill it, and reach the review "
                    "screen. The final Confirm step is risky and is gated in unattended replay.",
        status="draft",  # draft => risky replay is blocked even with confirm_risky (must be approved)
        target=Target(app_id="mock_cu", vendor="lyrebird-mock", entry_url_pattern="/member/*/subaccount"),
        inputs=[
            InputParam(name="member_id", type="string", example="100001"),
            InputParam(name="amount", type="string", example="250.00"),
        ],
        outputs=[],
        steps=[
            Step(index=0, action="type", target=_label("Initial deposit", "textbox"), value="{{amount}}",
                 postcondition=None, risk=Risk.SAFE),
            Step(index=1, action="select", target=_label("Account type", "combobox"), value="money_market", risk=Risk.SAFE),
            # radios/checkbox carry a state-suffixed name, so match by visible option text
            Step(index=2, action="click", target=_toggle("Electronic", "radio"), risk=Risk.SAFE),
            Step(index=3, action="click", target=_toggle("Member authorized this action", "checkbox"), risk=Risk.SAFE),
            Step(index=4, action="click", target=_role_name("button", "Continue to review"), risk=Risk.SAFE,
                 postcondition=ConditionDetector(by="text", match="Review")),
            # THE RISKY STEP: Confirm. Recorded as risky; replay gates it (never submits).
            Step(index=5, action="click", target=_role_name("button", "Confirm and open account"), risk=Risk.RISKY),
        ],
        known_conditions=[
            KnownCondition(code=OutcomeCode.VALIDATION_ERROR, klass=OutcomeClass.BUSINESS_OUTCOME,
                           detector=ConditionDetector(by="text", match="Validation error"), on_detect="return_outcome"),
        ],
        success_checkpoint=ConditionDetector(by="text", match="Review"),  # reaching review is success; Confirm is gated
        risk_summary="Contains a risky Confirm step (index 5); blocked in unattended replay.",
        provenance=Provenance(discovery_run_id="authored", model="none", created_at="2026-09-14T00:00:00Z",
                              viewport=Viewport(width=1280, height=900)),
    )


def main() -> None:
    path = CapabilityStore(ROOT / "artifacts").save(build())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
