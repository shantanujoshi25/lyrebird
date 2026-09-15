"""Condition detection — is a runtime state present, without the LLM.

A ConditionDetector matches against the current surface: `text` (substring of visible page
text), `role` (an element with that role exists), or `url_pattern` (glob over the URL). This
is how replay recognizes a known business outcome / recoverable condition deterministically
(R3), and how success checkpoints and per-step postconditions are verified.
"""

from __future__ import annotations

import fnmatch

from lyrebird.capability.schema import ConditionDetector
from lyrebird.surface.base import Observation


def detected(detector: ConditionDetector, obs: Observation, page_text: str) -> bool:
    if detector.by == "text":
        return detector.match.lower() in page_text.lower()
    if detector.by == "role":
        return any(e.role == detector.match for e in obs.elements)
    if detector.by == "url_pattern":
        return fnmatch.fnmatch(obs.url, detector.match) or detector.match in obs.url
    return False
