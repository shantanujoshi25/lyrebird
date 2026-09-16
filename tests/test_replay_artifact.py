"""C5 — replay the ACTUAL committed artifact end-to-end. No LLM, no tokens.

This is the criterion-#2 proof: the artifact produced by the real discovery run replays
deterministically and verifies success, with the balance extracted. It exercises the full
recorded flow (login -> search -> detail) via resolved locator candidates, not probes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lyrebird.capability.store import CapabilityStore
from lyrebird.policy import Policy
from lyrebird.replay.executor import ReplayContext, ReplayEngine
from lyrebird.surface.playwright_web import PlaywrightWebSurface

pytestmark = pytest.mark.slow  # browser-driven

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "policy.yaml")


@pytest.fixture
def surface(live_server: str) -> PlaywrightWebSurface:
    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    yield s
    s.close()


def test_committed_artifact_replays_and_extracts_balance(surface, live_server, monkeypatch) -> None:
    # the sensitive password is supplied at replay time from env, never from the artifact
    monkeypatch.setenv("LYREBIRD_PARAM_PASSWORD", "demo-pass-not-secret")

    cap = CapabilityStore(ROOT / "artifacts").load("lookup_savings_balance", 1)
    engine = ReplayEngine(cap, surface, POLICY)
    result = engine.run(ReplayContext(params={"username": "teller", "member_id": "100001"}))

    assert result.status == "SUCCESS", f"{result.status} {result.observed}"
    assert result.outputs.get("savings_balance") == 4210.75
    # drift telemetry: every acting step resolved (fallback depths recorded)
    assert result.fallback_depths, "expected per-step fallback depths"


def test_committed_artifact_fresh_param_other_member(surface, live_server, monkeypatch) -> None:
    monkeypatch.setenv("LYREBIRD_PARAM_PASSWORD", "demo-pass-not-secret")
    cap = CapabilityStore(ROOT / "artifacts").load("lookup_savings_balance", 1)
    engine = ReplayEngine(cap, surface, POLICY)
    # same artifact, different member id -> that member's balance (proves parameterization)
    result = engine.run(ReplayContext(params={"username": "teller", "member_id": "100003"}))
    assert result.status == "SUCCESS", f"{result.status} {result.observed}"
    assert result.outputs.get("savings_balance") == 15320.00
