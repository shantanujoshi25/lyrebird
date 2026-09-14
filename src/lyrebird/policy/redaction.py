"""Redaction — text scrub + screenshot masking (R4, A7).

Two of the three redaction layers (the third — referencing sensitive params by name and
reading their values from env — lives in the discovery/replay wiring). Redaction runs on
the write path *before* anything reaches evidence, so no unredacted value ever hits disk.

`redact_text`  — replace regex matches (SSN, long account numbers) and any explicitly
                 named sensitive values with a fixed marker. Idempotent (the marker itself
                 matches nothing), so re-redacting is a no-op.
`mask_screenshot` — overpaint given bounding boxes (the fields bound to sensitive params)
                 with a solid block before the screenshot is written.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

from lyrebird.policy.model import Policy

REDACTED = "[REDACTED]"


def redact_text(policy: Policy, text: str, *, sensitive_values: list[str] | None = None) -> str:
    """Scrub regex-matched patterns and any explicitly-named sensitive values from `text`."""
    out = text
    # 1. explicit sensitive values (e.g. the password read from env) — scrubbed verbatim.
    for val in sensitive_values or []:
        if val:
            out = out.replace(val, REDACTED)
    # 2. regex patterns from policy (SSN, long account-number-like runs).
    for pat in policy.redaction.scrub_patterns:
        out = re.sub(pat, REDACTED, out)
    return out


def mask_screenshot(src: Path | str, *, boxes: list[tuple[int, int, int, int]], out_path: Path | str) -> Path:
    """Overpaint each (x, y, w, h) box with a solid block; write to out_path and return it."""
    src, out_path = Path(src), Path(out_path)
    img = Image.open(src).convert("RGB")
    if boxes:
        draw = ImageDraw.Draw(img)
        for (x, y, w, h) in boxes:
            draw.rectangle([x, y, x + w, y + h], fill=(0, 0, 0))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path
