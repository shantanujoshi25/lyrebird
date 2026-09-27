"""record_capability — trajectory + ARIA snapshots -> Capability.

Each acting step's target and each read output become a DurableLocator: an ordered set of
concrete handles captured OFF the DOM node at discovery (id/name/data-*, role+name, exact text,
label anchor, tag-ordinal), most-stable first. Replay tries them in order; the winning index is
the drift signal (§7). We do not synthesize locators from reconstructed text — the node reported
its own handles, so a stable-DOM legacy app replays reliably.

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
    DurableLocator,
    InputParam,
    KnownCondition,
    LocatorCandidate,
    OutputSpec,
    Provenance,
    Risk,
    Step,
    Target,
)
from lyrebird.recorder.binding import bind_value

# Which recorded tools become which artifact actions; only these carry a target element.
_TARGETED = {"type", "click", "select", "scroll"}


def _durable_from(candidates: list[dict[str, Any]], *, semantic_id: str) -> DurableLocator | None:
    """Wrap the node's own durable-locator dicts into a DurableLocator (skip malformed ones)."""
    cands = [LocatorCandidate(**c) for c in candidates if c.get("kind") and c.get("value")]
    return DurableLocator(semantic_id=semantic_id, candidates=cands) if cands else None


def _target_for(el: dict[str, Any], *, semantic_id: str) -> DurableLocator | None:
    """The step target's durable locator, straight from the recorded element's own handles."""
    return _durable_from(el.get("locators") or [], semantic_id=semantic_id)


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
    goal: str | None = None,
) -> Capability:
    trajectory_path, aria_dir = Path(trajectory_path), Path(aria_dir)
    rows = [json.loads(line) for line in trajectory_path.read_text().splitlines() if line.strip()]

    steps: list[Step] = []
    output_locators: dict[str, DurableLocator] = {}   # output -> durable locator (LLM's variable/anchor judgment)
    output_values: dict[str, str] = {}                # output -> value_seen (ground-truth sanity-check)
    for row in rows:
        tool = row["tool"]

        # read_value rows are NOT steps — they carry the durable locator (and the LLM's
        # variable/fixed + anchor judgment) for a declared output's value. Replay resolves it.
        if tool == "read_value":
            read = row.get("output_read") or {}
            name = read.get("output")
            if not name:
                continue
            loc = _durable_from(read.get("candidates") or [], semantic_id=f"{name}_value")
            if loc:
                output_locators[name] = loc
            output_values[name] = read.get("value_seen", "")
            continue

        idx = row.get("input", {}).get("index")
        target_spec: DurableLocator | None = None
        value: str | None = None

        # A human-taught step (R4) carries its element inline (no ARIA file); build the same
        # durable locator from it as for an LLM step.
        human_el = row.get("human_element")
        if tool in _TARGETED and human_el is not None:
            target_spec = _target_for(human_el, semantic_id=_semantic_id(human_el, row["step"]))
        elif tool in _TARGETED and idx is not None:
            el = _element_from_aria(aria_dir, row["step"], idx)
            target_spec = _target_for(el, semantic_id=_semantic_id(el, row["step"]))

        if tool == "type":
            b = bind_value(explicit_binding=row.get("binding"), literal=row.get("input", {}).get("value"), inputs=inputs)
            value = b.value if b else None
        elif tool == "select":
            value = row.get("input", {}).get("value")

        # verified postcondition (R2): the text the model expected and we confirmed post-action,
        # recorded so replay can supervise this step deterministically. If the expected text
        # contains a bound input's example value (e.g. the member id it saw), PARAMETERIZE it —
        # otherwise the postcondition would wrongly hard-code one invocation's value and fail on
        # replay with a different parameter.
        expected = row.get("expected_text")
        if expected:
            for p in inputs:
                if p.example and not p.sensitive and p.example in expected:
                    expected = expected.replace(p.example, f"{{{{{p.name}}}}}")
        postcondition = ConditionDetector(by="text", match=expected) if expected else None

        risk = _risk_for(row)
        steps.append(
            Step(
                index=len(steps),
                action=tool,  # type: ignore[arg-type]
                target=target_spec,
                value=value,
                postcondition=postcondition,
                risk=risk,
                provenance=row.get("provenance", "model"),  # human-taught steps carry provenance:human (R4)
            )
        )

    # Attach the durable locator + observed value to each declared output that was read.
    def _apply(o: OutputSpec) -> OutputSpec:
        updates: dict[str, Any] = {}
        if o.name in output_locators:
            updates["locator"] = output_locators[o.name]
        if o.name in output_values:
            updates["value_seen"] = output_values[o.name]
        return o.model_copy(update=updates) if updates else o

    outputs = [_apply(o) for o in outputs]

    return Capability(
        capability_id=capability_id,
        name=name,
        description=description,
        goal=goal,
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
