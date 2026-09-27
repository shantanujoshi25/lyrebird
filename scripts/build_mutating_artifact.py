"""Build the mutating 'open sub-account' capability (up to the risky Confirm).

This capability carries the safety + escalation story: open the sub-account form -> fill the
multi-field form -> reach the review page, where the final Confirm step is classified RISKY and,
in unattended replay, blocks/escalates rather than clicking. **Nothing ever submits** (there is
no POST handler for /confirm in the mock — reaching it would 404, which is the point: we never do).

It's authored directly (not from an LLM discovery run) because this artifact exists to exercise
the risky-step GATE and the human-handoff mechanism, not to re-prove discovery (the lookup
capability already does that with a real LLM run). The durable locators below were read off the
live mock DOM, so they resolve deterministically at replay. Run: `uv run python
scripts/build_mutating_artifact.py`. No key needed.
"""

from __future__ import annotations

from pathlib import Path

from lyrebird.capability.schema import (
    Capability, ConditionDetector, DurableLocator, InputParam, KnownCondition,
    LocatorCandidate, OutcomeClass, OutcomeCode, Provenance, Risk, Step, Target, Viewport,
)
from lyrebird.capability.store import CapabilityStore

ROOT = Path(__file__).resolve().parents[1]


def _loc(semantic_id: str, *candidates: LocatorCandidate) -> DurableLocator:
    return DurableLocator(semantic_id=semantic_id, candidates=list(candidates))


def build() -> Capability:
    return Capability(
        capability_id="open_subaccount",
        name="Open a member sub-account (to review)",
        description="Open the sub-account form for a member, fill it, and reach the review "
                    "screen. The final Confirm step is risky and is gated in unattended replay.",
        status="draft",  # draft => risky replay is blocked even with confirm_risky (must be approved)
        goal="Open a new sub-account for a member and reach the confirmation/review screen.",
        target=Target(app_id="mock_cu", vendor="lyrebird-mock", entry_url_pattern="/member/*/subaccount"),
        inputs=[
            InputParam(name="member_id", type="string", example="100001"),
            InputParam(name="amount", type="string", example="250.00"),
        ],
        outputs=[],
        steps=[
            # durable locators read off the live form; css[name=…] is unique for the single-value
            # fields, role+name for the radios/checkbox (whose name carries the option text).
            Step(index=0, action="type",
                 target=_loc("amount_input", LocatorCandidate(kind="css", value='input[name="amount"]')),
                 value="{{amount}}", risk=Risk.SAFE),
            Step(index=1, action="select",
                 target=_loc("acct_type_select", LocatorCandidate(kind="css", value='select[name="acct_type"]')),
                 value="money_market", risk=Risk.SAFE),
            # radios/checkbox have no <label for>, so their accessible name is empty; the unique
            # durable handle is the name+value attribute pair.
            Step(index=2, action="click",
                 target=_loc("statement_electronic",
                             LocatorCandidate(kind="css", value='input[name="statement"][value="electronic"]')),
                 risk=Risk.SAFE),
            Step(index=3, action="click",
                 target=_loc("authorized_checkbox", LocatorCandidate(kind="css", value='input[name="authorized"]')),
                 risk=Risk.SAFE),
            Step(index=4, action="click",
                 target=_loc("continue_review",
                             LocatorCandidate(kind="role", value="button", name="Continue to review"),
                             LocatorCandidate(kind="text", value="Continue to review")),
                 risk=Risk.SAFE, postcondition=ConditionDetector(by="text", match="Review")),
            # THE RISKY STEP: Confirm. Classified risky; unattended replay gates it -> escalates,
            # never clicks. The mock has no POST /confirm handler, so a submit would 404 anyway.
            Step(index=5, action="click",
                 target=_loc("confirm_button",
                             LocatorCandidate(kind="role", value="button", name="Confirm and open account"),
                             LocatorCandidate(kind="text", value="Confirm and open account")),
                 risk=Risk.RISKY),
        ],
        known_conditions=[
            KnownCondition(code=OutcomeCode.VALIDATION_ERROR, klass=OutcomeClass.BUSINESS_OUTCOME,
                           detector=ConditionDetector(by="text", match="Validation error"),
                           on_detect="return_outcome"),
        ],
        success_checkpoint=ConditionDetector(by="text", match="Review"),  # reaching review is success; Confirm is gated
        risk_summary="Contains a risky Confirm step (index 5); blocked in unattended replay.",
        provenance=Provenance(discovery_run_id="authored", model="none",
                              created_at="2026-09-27T00:00:00Z",
                              viewport=Viewport(width=1280, height=900)),
    )


def main() -> None:
    path = CapabilityStore(ROOT / "artifacts").save(build())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
