"""Regenerate the committed Capability artifact from the banked discovery trajectory.

Run without a key: `uv run python scripts/build_artifact.py`. This is the "run without live
services" path — it turns the committed discovery evidence into artifacts/<id>/v1.json using
only the saved trajectory + ARIA snapshots, no browser and no LLM.
"""

from __future__ import annotations

from pathlib import Path

from lyrebird.capability.schema import (
    ConditionDetector,
    InputParam,
    KnownCondition,
    OutcomeClass,
    OutcomeCode,
    OutputSpec,
    Provenance,
    Target,
    Viewport,
)
from lyrebird.capability.store import CapabilityStore
from lyrebird.recorder import record_capability

ROOT = Path(__file__).resolve().parents[1]
# Source from the latest committed mock discovery run (127.0.0.1 = the local mock app), which
# carries the verify-loop postconditions + the tag-agnostic output anchor (redesign R2).
EVIDENCE = sorted((ROOT / "evidence").glob("discover-127_0_0_1-*"))[-1]
TRAJ = EVIDENCE / "steps.jsonl"


def main() -> None:
    cap = record_capability(
        TRAJ,
        EVIDENCE / "aria",
        capability_id="lookup_savings_balance",
        name="Look up member savings balance",
        description="Sign in, search a member by ID, and read the savings balance.",
        goal="Sign in, look up member 100001, and read their savings balance.",
        inputs=[
            InputParam(name="username", type="string", example="teller"),
            InputParam(name="password", type="string", sensitive=True),
            InputParam(name="member_id", type="string", example="100001"),
        ],
        # The extraction expression is authored at discovery (read_value) and attached by the
        # recorder from the trajectory — so we declare only name+type here.
        outputs=[OutputSpec(name="savings_balance", type="number")],
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
