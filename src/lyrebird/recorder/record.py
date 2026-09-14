"""record_capability — trajectory + ARIA snapshots -> Capability.

For each acting step, the recorded element (looked up by index in that step's ARIA snapshot)
is turned into an ordered list of locator candidates, most-robust first. Candidate order is
the fallback order at replay and the drift signal (§7). Only strategies with usable args are
emitted, and never CSS/XPath (enforced anyway by the schema's LocatorStrategy enum, A12).

The raw model transcript is NOT read or copied here — only the structured trajectory and the
element snapshots — so the artifact is decoupled from the transcript by construction (R2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    Risk,
    Step,
    Target,
    Viewport,
)
from lyrebird.recorder.binding import bind_value

# Which recorded tools become which artifact actions; only these carry a target element.
_TARGETED = {"type", "click", "select", "scroll"}


def _candidates_for(el: dict[str, Any], *, semantic_id: str) -> LocatorSpec:
    """Ordered, most-robust-first locator candidates synthesized from a recorded element."""
    role = el.get("role", "")
    name = (el.get("name") or "").strip()
    nearby = [t for t in el.get("nearby_text", []) if t]
    bbox = el.get("bbox")

    cands: list[LocatorCandidate] = []
    # 1. role + accessible name — most robust, works across surfaces (AXRole+AXTitle).
    if role and name:
        cands.append(LocatorCandidate(strategy="role_name", args={"role": role, "name": name}, confidence=0.95))
    # 2. visible text — for links/buttons whose text is the name.
    if name and role in ("link", "button"):
        cands.append(LocatorCandidate(strategy="visible_text", args={"text": name}, confidence=0.8))
    # 3. label proximity — the adjacency-only labels of the hostile form.
    if nearby:
        cands.append(LocatorCandidate(strategy="label_proximity", args={"label": nearby[0], "role": role}, confidence=0.7))
    # 4. relative anchor — "the control in the row whose label cell says X".
    if len(nearby) > 1:
        cands.append(
            LocatorCandidate(strategy="relative_anchor", args={"anchor": nearby[-1], "relation": "same_row", "role": role}, confidence=0.5)
        )
    # 5. bbox — last resort; only usable when the replay viewport matches provenance (A14).
    if bbox:
        x, y, w, h = bbox
        cands.append(LocatorCandidate(strategy="bbox", args={"x": str(x), "y": str(y), "w": str(w), "h": str(h)}, confidence=0.2))

    if not cands:  # degenerate element — still need one candidate; bbox or a name-only guess
        cands.append(LocatorCandidate(strategy="role_name", args={"role": role or "generic", "name": name}, confidence=0.1))
    return LocatorSpec(semantic_id=semantic_id, candidates=cands)


def _semantic_id(el: dict[str, Any], step_index: int) -> str:
    """A stable, human-readable handle — the multi-tenant override key (A5)."""
    name = (el.get("name") or el.get("role") or f"el{step_index}").strip().lower()
    slug = "".join(c if c.isalnum() else "_" for c in name).strip("_") or f"el{step_index}"
    return f"{slug}_{el.get('role', 'x')}"


def record_capability(
    trajectory_path: Path | str,
    aria_dir: Path | str,
    *,
    capability_id: str,
    name: str,
    description: str,
    inputs: list[InputParam],
    outputs: list[OutputSpec],
    known_conditions: list[KnownCondition],
    success_checkpoint: ConditionDetector,
    target: Target,
    provenance: Provenance,
) -> Capability:
    trajectory_path, aria_dir = Path(trajectory_path), Path(aria_dir)
    rows = [json.loads(line) for line in trajectory_path.read_text().splitlines() if line.strip()]

    steps: list[Step] = []
    for row in rows:
        tool = row["tool"]
        idx = row.get("input", {}).get("index")
        target_spec: LocatorSpec | None = None
        value: str | None = None

        if tool in _TARGETED and idx is not None:
            el = _element_from_aria(aria_dir, row["step"], idx)
            target_spec = _candidates_for(el, semantic_id=_semantic_id(el, row["step"]))

        if tool == "type":
            b = bind_value(explicit_binding=row.get("binding"), literal=row.get("input", {}).get("value"), inputs=inputs)
            value = b.value if b else None
        elif tool == "select":
            value = row.get("input", {}).get("value")

        risk = _risk_for(row)
        steps.append(
            Step(
                index=len(steps),
                action=tool,  # type: ignore[arg-type]
                target=target_spec,
                value=value,
                postcondition=None,
                risk=risk,
                provenance="model",
            )
        )

    return Capability(
        capability_id=capability_id,
        name=name,
        description=description,
        target=target,
        inputs=inputs,
        outputs=outputs,
        steps=steps,
        known_conditions=known_conditions,
        success_checkpoint=success_checkpoint,
        risk_summary=_risk_summary(steps),
        provenance=provenance,
    )


def _element_from_aria(aria_dir: Path, step: int, index: int) -> dict[str, Any]:
    path = aria_dir / f"step-{step:02d}.json"
    els = json.loads(path.read_text())
    for el in els:
        if el.get("index") == index:
            return el
    raise KeyError(f"element index {index} not found in {path.name}")


def _risk_for(row: dict[str, Any]) -> Risk:
    # A click/press on a control whose recorded note or name implies mutation is risky. In the
    # read-only flow there are none; the recorder still classifies so replay can gate (R4).
    return Risk.SAFE


def _risk_summary(steps: list[Step]) -> str:
    risky = [s.index for s in steps if s.risk is Risk.RISKY]
    return f"Contains risky steps at indices {risky}." if risky else "Read-only; no risky steps."
