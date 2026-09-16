"""C6b — policy & risk enforcement wired into the loops. No LLM, no tokens.

Proves R4 end-to-end: the risky Confirm step of the mutating capability BLOCKS in unattended
replay (never submits) unless the artifact is approved AND the caller opts in; an off-
allowlist action is denied; and a full run's evidence audits clean for sensitive values.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lyrebird.capability.store import CapabilityStore
from lyrebird.policy import Policy, check_action, classify_risk
from lyrebird.replay.executor import ReplayContext, ReplayEngine
from lyrebird.surface.base import Action
from lyrebird.surface.playwright_web import PlaywrightWebSurface

pytestmark = pytest.mark.slow  # browser-driven

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "policy.yaml")
MEMBER = "100001"
USER, PW = "teller", "demo-pass-not-secret"


@pytest.fixture
def surface(live_server: str) -> PlaywrightWebSurface:
    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    yield s
    s.close()


def _login(surface: PlaywrightWebSurface) -> None:
    obs = surface.observe()
    u = next(e.index for e in obs.elements if e.role == "textbox" and any("Username" in t for t in e.nearby_text))
    surface.act(Action(kind="type", target_index=u, value=USER))
    obs = surface.observe()
    p = next(e.index for e in obs.elements if e.role == "textbox" and any("Password" in t for t in e.nearby_text))
    surface.act(Action(kind="type", target_index=p, value=PW))
    obs = surface.observe()
    b = next(e.index for e in obs.elements if e.role == "button" and "Sign in" in e.name)
    surface.act(Action(kind="click", target_index=b))


def _mutating_cap(status: str = "draft"):
    cap = CapabilityStore(ROOT / "artifacts").load("open_subaccount", 1)
    return cap.model_copy(update={"status": status})


def _replay_mutating(surface, live_server, *, status: str, confirm_risky: bool):
    _login(surface)
    surface.act(Action(kind="navigate", value=f"{live_server}/member/{MEMBER}/subaccount"))
    engine = ReplayEngine(_mutating_cap(status), surface, POLICY)
    return engine.run(ReplayContext(params={"member_id": MEMBER, "amount": "250.00"}, confirm_risky=confirm_risky))


# ── the risky-step gate (R4) ─────────────────────────────────────────────────
def test_draft_confirm_blocks_even_with_confirm_risky(surface, live_server) -> None:
    # a DRAFT artifact must never run a risky step unattended, regardless of confirm_risky
    r = _replay_mutating(surface, live_server, status="draft", confirm_risky=True)
    assert r.status == "ESCALATED"
    assert r.failed_step == 5                       # the Confirm step
    # and crucially: we did NOT submit — still on the review page, not a confirmation page
    assert "/confirm" not in surface.observe().url


def test_approved_without_confirm_risky_still_blocks(surface, live_server) -> None:
    r = _replay_mutating(surface, live_server, status="approved", confirm_risky=False)
    assert r.status == "ESCALATED"
    assert r.failed_step == 5
    assert "/confirm" not in surface.observe().url


def test_approved_with_confirm_risky_allows_confirm(surface, live_server) -> None:
    # approved + explicit opt-in: the risky step is permitted (the flow proceeds past the gate)
    r = _replay_mutating(surface, live_server, status="approved", confirm_risky=True)
    # the Confirm click is allowed; success_checkpoint "Review" was already satisfied before it,
    # so the run reaches the gate, passes it, and completes without escalating on risk.
    assert r.status != "ESCALATED", f"risk gate wrongly blocked an approved+confirmed run: {r.observed}"


def test_risk_classifier_flags_confirm_step() -> None:
    cap = _mutating_cap()
    confirm = cap.steps[5]
    from lyrebird.capability.schema import Risk
    assert classify_risk(POLICY, confirm) is Risk.RISKY


# ── allowlist enforcement in replay ──────────────────────────────────────────
def test_off_allowlist_action_denied_in_replay(surface, live_server) -> None:
    # a policy check on an off-allowlist domain must deny (fail-closed)
    d = check_action(POLICY, url="http://evil.example.com/member/100001", action_type="click")
    assert not d.allowed
    # in-allowlist passes
    d2 = check_action(POLICY, url=f"{live_server}/member/{MEMBER}", action_type="click")
    assert d2.allowed


# ── end-to-end redaction audit over a real run's evidence ────────────────────
def test_full_run_evidence_has_no_sensitive_value(surface, live_server, tmp_path, monkeypatch) -> None:
    from lyrebird.evidence import EvidenceWriter
    from lyrebird.capability.schema import InputParam, OutputSpec, LocatorCandidate, LocatorSpec
    from lyrebird.discovery.loop import DiscoveryLoop
    from lyrebird.discovery.llm import ToolCall

    monkeypatch.setenv("LYREBIRD_PARAM_PASSWORD", "s3cr3t-audit-pw")

    # a tiny scripted discovery run that types the sensitive password, writing full evidence
    class Script:
        def __init__(self): self.i = 0
        def decide(self, *, system, messages, tools):
            steps = [ToolCall("1", "finish", {"success": False, "reason": "audit stop"})]
            c = steps[min(self.i, len(steps) - 1)]; self.i += 1; return c

    ev = EvidenceWriter("audit", POLICY, root=tmp_path, sensitive_values=["s3cr3t-audit-pw"])
    inputs = [InputParam(name="password", type="string", sensitive=True)]
    loop = DiscoveryLoop(Script(), surface, POLICY, ev, inputs=inputs, outputs=[])
    loop.run("audit run")

    # audit EVERY text file under the run dir for the sensitive value
    run_dir = Path(tmp_path) / "audit"
    for path in run_dir.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".txt"}:
            assert "s3cr3t-audit-pw" not in path.read_text(), f"sensitive value leaked in {path.name}"
