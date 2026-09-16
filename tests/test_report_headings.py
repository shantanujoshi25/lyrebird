"""C9 — guard REPORT.md's mandated seven headings, in order.

REPORT.md is required by the brief with exactly these seven H2 headings in this order. This
test asserts that. It SKIPS when REPORT.md is absent (it is kept local-only / gitignored in
this working copy), so a clean checkout without the file doesn't fail — but the moment the
file exists, its headings are validated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPORT = Path(__file__).resolve().parents[1] / "REPORT.md"

EXPECTED = [
    "Architecture",
    "Artifact schema",
    "Determinism & error handling",
    "Heterogeneity & multi-tenant",
    "Escalation & handoff",
    "Safety",
    "Cuts",
]


@pytest.mark.skipif(not REPORT.exists(), reason="REPORT.md is kept local-only")
def test_report_has_the_seven_headings_in_order() -> None:
    text = REPORT.read_text()
    # collect H2 headings, stripping any leading "N. " numbering
    headings = [re.sub(r"^\d+\.\s*", "", m.strip()) for m in re.findall(r"^##\s+(.*)$", text, re.M)]
    assert headings == EXPECTED, f"REPORT headings/order wrong:\n  got: {headings}\n  want: {EXPECTED}"
