"""Locator resolution — the determinism core (R3, §7).

Walk a LocatorSpec's candidates in order. Each candidate filters the current observation's
elements; a candidate that matches EXACTLY ONE element wins and its index in the candidate
list is the fallback depth (the drift signal — depth 0 means the primary locator still
works; a deeper hit means the UI shifted). Zero matches -> try the next candidate. More than
one match -> that candidate is too weak; skip it (ambiguity is not a unique target). If no
candidate yields a unique match, resolution fails (LOCATOR_NOT_FOUND, a hard failure).

bbox is only honored when the replay viewport matches the recorded one (A14) — otherwise the
coordinates mean something different and using them would be non-deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from lyrebird.capability.schema import LocatorCandidate, LocatorSpec, LocatorStrategy
from lyrebird.surface.base import Element, Observation, Viewport


class ResolveError(Exception):
    """Raised when no candidate yields a unique match — a hard LOCATOR_NOT_FOUND."""


@dataclass
class Resolution:
    element: Element
    index: int
    fallback_depth: int          # which candidate matched (0 = primary)
    strategy: LocatorStrategy


def resolve(spec: LocatorSpec, obs: Observation, *, recorded_viewport: Viewport, live_viewport: Viewport) -> Resolution:
    for depth, cand in enumerate(spec.candidates):
        matches = _match(cand, obs, recorded_viewport=recorded_viewport, live_viewport=live_viewport)
        if len(matches) == 1:
            el = matches[0]
            return Resolution(element=el, index=el.index, fallback_depth=depth, strategy=cand.strategy)
        # 0 matches -> try next; >1 -> too ambiguous, try next
    raise ResolveError(f"no unique match for semantic_id={spec.semantic_id!r} across {len(spec.candidates)} candidates")


def _match(cand: LocatorCandidate, obs: Observation, *, recorded_viewport: Viewport, live_viewport: Viewport) -> list[Element]:
    a = cand.args
    els = obs.elements
    if cand.strategy == LocatorStrategy.ROLE_NAME:
        return [e for e in els if e.role == a.get("role") and e.name == a.get("name")]
    if cand.strategy == LocatorStrategy.VISIBLE_TEXT:
        text = (a.get("text") or "").lower()
        return [e for e in els if text and text in e.name.lower()]
    if cand.strategy == LocatorStrategy.LABEL_PROXIMITY:
        label = (a.get("label") or "").lower()
        role = a.get("role")
        out = [e for e in els if any(label == t.lower() for t in e.nearby_text)]
        if role:
            out = [e for e in out if e.role == role]
        return out
    if cand.strategy == LocatorStrategy.RELATIVE_ANCHOR:
        anchor = (a.get("anchor") or "").lower()
        role = a.get("role")
        out = [e for e in els if any(anchor in t.lower() for t in e.nearby_text)]
        if role:
            out = [e for e in out if e.role == role]
        return out
    if cand.strategy == LocatorStrategy.BBOX:
        # only trust coordinates when the viewport matches the recording (A14)
        if (recorded_viewport.width, recorded_viewport.height) != (live_viewport.width, live_viewport.height):
            return []
        try:
            target = (int(a["x"]), int(a["y"]), int(a["w"]), int(a["h"]))
        except (KeyError, ValueError):
            return []
        return [e for e in els if tuple(e.bbox) == target]
    return []
