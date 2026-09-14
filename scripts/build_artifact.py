"""Regenerate the committed Capability artifact from the banked discovery trajectory.

Run without a key: `uv run python scripts/build_artifact.py`. This is the "run without live
services" path — it turns the committed discovery evidence into artifacts/<id>/v1.json using
only the saved trajectory + ARIA snapshots, no browser and no LLM.
"""

from __future__ import annotations

from pathlib import Path

from lyrebird.capability.schema import (
    ConditionDetector, InputParam, KnownCondition, LocatorCandidate, LocatorSpec,
    OutcomeClass, OutcomeCode, OutputSpec, Provenance, Target, Viewport,
)
from lyrebird.capability.store import CapabilityStore
from lyrebird.recorder import record_capability

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = next((ROOT / "evidence").glob("discover-lookup-*"))
TRAJ = ROOT / "tests" / "fixtures" / "discovery_trajectory.jsonl"


def main() -> None:
    cap = record_capability(
        TRAJ,
        EVIDENCE / "aria",
        capability_id="lookup_savings_balance",
        name="Look up member savings balance",
        description="Sign in, search a member by ID, and read the savings balance.",
        inputs=[
            InputParam(name="username", type="string", example="teller"),
            InputParam(name="password", type="string", sensitive=True),
            InputParam(name="member_id", type="string", example="100001"),
        ],
        outputs=[
            OutputSpec(
                name="savings_balance", type="number", transform="currency",
                extract=LocatorSpec(
                    semantic_id="savings_balance_cell",
                    candidates=[LocatorCandidate(strategy="label_proximity", args={"label": "Savings"}, confidence=0.9)],
                ),
            )
        ],
        known_conditions=[
            KnownCondition(
                code=OutcomeCode.NOT_FOUND, klass=OutcomeClass.BUSINESS_OUTCOME,
                detector=ConditionDetector(by="text", match="No such member"), on_detect="return_outcome",
            )
        ],
        success_checkpoint=ConditionDetector(by="text", match="Savings"),
        target=Target(app_id="mock_cu", vendor="lyrebird-mock", entry_url_pattern="/member", tenant="base"),
        provenance=Provenance(
            discovery_run_id=EVIDENCE.name, model="claude-sonnet-4-6",
            created_at="2026-09-14T00:00:00Z",
            viewport=Viewport(width=1280, height=900, device_scale_factor=1.0),
        ),
    )
    path = CapabilityStore(ROOT / "artifacts").save(cap)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
