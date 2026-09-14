"""policy/ — safety primitives: allowlist, risk classification, redaction.

Three pure functions over actions / steps / text / images, plus a typed Policy loaded from
policy.yaml (fail-closed: anything not explicitly allowed is denied). Called *by* the
discovery and replay loops (wiring is C6b); this module calls neither. No browser, no LLM.
See docs/01_ARCHITECTURE.md §6.
"""

from lyrebird.policy.allowlist import Decision, check_action
from lyrebird.policy.model import Policy
from lyrebird.policy.redaction import mask_screenshot, redact_text
from lyrebird.policy.risk import classify_risk

__all__ = [
    "Decision",
    "Policy",
    "check_action",
    "classify_risk",
    "mask_screenshot",
    "redact_text",
]
