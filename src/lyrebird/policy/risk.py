"""classify_risk — safe (reversible) vs risky (irreversible) for a recorded Step.

A step is risky when it activates a control whose accessible name (or value) matches a
risky text pattern (submit / confirm / transfer / delete / close account) — i.e. the
mutating actions. Reads and navigation are always safe. This gates unattended replay (R4):
a risky step is blocked unless the artifact is approved and the caller passes
confirm_risky=true (enforced in C6b).
"""

from __future__ import annotations

from lyrebird.capability.schema import Risk, Step
from lyrebird.policy.model import Policy

# Only activation actions can be risky; typing/scrolling/navigating never mutate on their own.
_ACTIVATING = {"click", "press"}


def classify_risk(policy: Policy, step: Step) -> Risk:
    if step.action not in _ACTIVATING:
        return Risk.SAFE

    haystack = _step_text(step).lower()
    for pat in policy.risk.risky_text_patterns:
        if pat.lower() in haystack:
            return Risk.RISKY
    return Risk.SAFE


def _step_text(step: Step) -> str:
    """All human-readable text associated with the step's target (names + value)."""
    parts: list[str] = []
    if step.value:
        parts.append(step.value)
    if step.target:
        for cand in step.target.candidates:
            parts.extend(str(v) for v in cand.args.values())
    return " ".join(parts)
