"""C6a — allowlist + risk classifier. Pure functions, no browser, no LLM.

These pin the safety contract (R4): the agent must not act outside the allowlist, and
risky/irreversible actions must be identifiable. Enforcement wiring into the loops is C6b;
here we test the primitives in isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lyrebird.policy import Policy, check_action, classify_risk
from lyrebird.capability.schema import DurableLocator, LocatorCandidate, Risk, Step

POLICY_YAML = Path(__file__).resolve().parents[1] / "policy.yaml"


@pytest.fixture
def policy() -> Policy:
    return Policy.load(POLICY_YAML)


# ── allowlist (fail-closed) ──────────────────────────────────────────────────
def test_allows_in_list_domain_route_and_action(policy: Policy) -> None:
    d = check_action(policy, url="http://127.0.0.1:8000/member/100001", action_type="click")
    assert d.allowed, d.reason


def test_denies_off_allowlist_domain(policy: Policy) -> None:
    d = check_action(policy, url="http://evil.example.com/member/100001", action_type="click")
    assert not d.allowed
    assert "domain" in d.reason.lower()


def test_denies_off_allowlist_route(policy: Policy) -> None:
    d = check_action(policy, url="http://127.0.0.1:8000/admin/wipe", action_type="click")
    assert not d.allowed
    assert "route" in d.reason.lower() or "url" in d.reason.lower()


def test_denies_off_allowlist_action_type(policy: Policy) -> None:
    d = check_action(policy, url="http://127.0.0.1:8000/member", action_type="drag")
    assert not d.allowed
    assert "action" in d.reason.lower()


def test_fail_closed_on_empty_url(policy: Policy) -> None:
    d = check_action(policy, url="", action_type="click")
    assert not d.allowed


# ── risk classifier ──────────────────────────────────────────────────────────
def _button_step(name: str) -> Step:
    return Step(
        index=0,
        action="click",
        target=DurableLocator(
            semantic_id="btn",
            candidates=[LocatorCandidate(kind="role", value="button", name=name)],
        ),
    )


@pytest.mark.parametrize("name", ["Confirm and open account", "Submit transfer", "Delete member", "Close account"])
def test_risky_button_names_flagged(policy: Policy, name: str) -> None:
    assert classify_risk(policy, _button_step(name)) is Risk.RISKY


@pytest.mark.parametrize("name", ["Search", "Continue to review", "Dismiss", "Sign in"])
def test_safe_button_names_not_flagged(policy: Policy, name: str) -> None:
    assert classify_risk(policy, _button_step(name)) is Risk.SAFE


def test_non_button_actions_are_safe(policy: Policy) -> None:
    # typing into a field is never risky regardless of surrounding text
    step = Step(index=0, action="type", value="{{member_id}}")
    assert classify_risk(policy, step) is Risk.SAFE


def test_policy_load_is_typed_and_failclosed_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Policy.load(tmp_path / "nope.yaml")
