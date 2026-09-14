"""C6a — redaction: text scrub + screenshot masking. Pure functions, no browser, no LLM.

R4: never persist secrets or raw sensitive data (creds/tokens/full PII) in artifacts, logs,
or screenshots. These tests prove the three redaction layers work in isolation; the
end-to-end evidence audit is C6b/Operations.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from lyrebird.policy import Policy, mask_screenshot, redact_text

POLICY_YAML = Path(__file__).resolve().parents[1] / "policy.yaml"


@pytest.fixture
def policy() -> Policy:
    return Policy.load(POLICY_YAML)


# ── text scrubbing ───────────────────────────────────────────────────────────
def test_scrubs_ssn(policy: Policy) -> None:
    out = redact_text(policy, "member SSN is 123-45-6789 on file")
    assert "123-45-6789" not in out
    assert "member SSN is" in out          # non-sensitive text preserved


def test_scrubs_long_account_number(policy: Policy) -> None:
    out = redact_text(policy, "acct 1234567890123 balance ok")
    assert "1234567890123" not in out


def test_keeps_short_identifiers(policy: Policy) -> None:
    # member IDs are identifiers, not secrets (A7) — a 6-digit id must NOT be scrubbed
    out = redact_text(policy, "member 100001 savings")
    assert "100001" in out


def test_scrubs_named_sensitive_values(policy: Policy) -> None:
    # a value passed for a named-sensitive param (e.g. the password) must be scrubbed even
    # if it doesn't match a regex pattern.
    out = redact_text(policy, "logged in with s3cr3t-pw-value", sensitive_values=["s3cr3t-pw-value"])
    assert "s3cr3t-pw-value" not in out


def test_redact_is_idempotent(policy: Policy) -> None:
    once = redact_text(policy, "SSN 123-45-6789")
    twice = redact_text(policy, once)
    assert once == twice


# ── screenshot masking ────────────────────────────────────────────────────────
def test_masks_bbox_region(policy: Policy, tmp_path: Path) -> None:
    # a solid white 100x100 image; mask a 20x20 box and assert those pixels changed.
    img = Image.new("RGB", (100, 100), (255, 255, 255))
    src = tmp_path / "shot.png"
    img.save(src)

    masked_path = mask_screenshot(src, boxes=[(10, 10, 20, 20)], out_path=tmp_path / "masked.png")
    masked = Image.open(masked_path).convert("RGB")

    # inside the box: not white anymore
    assert masked.getpixel((15, 15)) != (255, 255, 255)
    # outside the box: untouched
    assert masked.getpixel((90, 90)) == (255, 255, 255)


def test_mask_no_boxes_is_noop_copy(policy: Policy, tmp_path: Path) -> None:
    img = Image.new("RGB", (10, 10), (0, 128, 255))
    src = tmp_path / "s.png"
    img.save(src)
    out = mask_screenshot(src, boxes=[], out_path=tmp_path / "o.png")
    assert Image.open(out).convert("RGB").getpixel((5, 5)) == (0, 128, 255)
