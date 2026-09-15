"""C7 — handoff: state machine, cross-process control transfer, same-session human capture.

No LLM, no tokens. The 'human' and the 'operator' are simulated (a background thread writes
the run-state file just as the separate operator process would), so no real human is needed
in CI — but the control-transfer MECHANISM (a polled run-state file) is the real one.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from lyrebird.handoff.capture import HumanCapture
from lyrebird.handoff.controller import Control, IllegalTransition, SessionController
from lyrebird.handoff.intervention import InterventionRequest
from lyrebird.handoff.runstate import RunState
from lyrebird.policy import Policy
from lyrebird.surface.base import Action
from lyrebird.surface.playwright_web import PlaywrightWebSurface

ROOT = Path(__file__).resolve().parents[1]
POLICY = Policy.load(ROOT / "policy.yaml")


# ── state machine ─────────────────────────────────────────────────────────────
def test_initial_control_is_automation(tmp_path) -> None:
    c = SessionController("r", tmp_path)
    assert c.control is Control.AUTOMATION
    assert RunState.read(tmp_path).control is Control.AUTOMATION  # answerable from the file


def test_illegal_transition_rejected(tmp_path) -> None:
    c = SessionController("r", tmp_path)
    c.finish()  # AUTOMATION -> DONE
    with pytest.raises(IllegalTransition):
        c.finish()  # DONE -> DONE is illegal


def test_operator_cannot_take_when_not_pending(tmp_path) -> None:
    SessionController("r", tmp_path)  # AUTOMATION
    with pytest.raises(IllegalTransition):
        SessionController.operator_take(tmp_path, "op")


# ── full escalate -> take -> handback -> resume, cross-process ────────────────
def test_escalate_take_handback_resume(tmp_path) -> None:
    c = SessionController("r", tmp_path)

    # a background "operator process": wait for PENDING_HUMAN, take, do a step, hand back.
    human_did_something: list[str] = []

    def operator() -> None:
        RunState.poll_until(tmp_path, Control.PENDING_HUMAN, timeout_s=5)
        SessionController.operator_take(tmp_path, "alice")
        human_did_something.append("performed manual step")
        time.sleep(0.05)
        SessionController.operator_handback(tmp_path, note="did the thing")

    t = threading.Thread(target=operator, daemon=True)
    t.start()

    checkpoint_calls: list[int] = []
    def checkpoint() -> bool:
        checkpoint_calls.append(1)
        return True  # human reached the expected state

    result = c.escalate(
        step=3, reason="stuck: repeated state", url="http://127.0.0.1/member",
        screenshot="screenshots/step-03.png", state_digest="member search",
        capability="lookup_savings_balance", timeout_s=5, on_resume_checkpoint=checkpoint,
    )
    t.join(timeout=5)

    assert result is Control.AUTOMATION            # resumed
    assert human_did_something == ["performed manual step"]
    assert checkpoint_calls == [1]                 # checkpoint re-verified on handback
    # evidence preserved: intervention request written with context
    req = InterventionRequest.read(tmp_path)
    assert req.step == 3 and "stuck" in req.reason
    # final control is AUTOMATION and recorded who held it
    assert RunState.read(tmp_path).operator == "alice"


def test_resume_blocked_when_checkpoint_fails(tmp_path) -> None:
    c = SessionController("r", tmp_path)

    def operator() -> None:
        RunState.poll_until(tmp_path, Control.PENDING_HUMAN, timeout_s=5)
        SessionController.operator_take(tmp_path, "bob")
        SessionController.operator_handback(tmp_path)

    threading.Thread(target=operator, daemon=True).start()
    result = c.escalate(
        step=1, reason="hard", url="u", screenshot="s", state_digest="d",
        capability="cap", timeout_s=5, on_resume_checkpoint=lambda: False,  # human did NOT reach it
    )
    # checkpoint failed -> do not resume automation; back to PENDING_HUMAN
    assert result is Control.PENDING_HUMAN


def test_escalate_no_wait_returns_pending(tmp_path) -> None:
    c = SessionController("r", tmp_path)
    result = c.escalate(step=0, reason="risky", url="u", screenshot="s", state_digest="d",
                        capability="cap", wait=False)
    assert result is Control.PENDING_HUMAN
    assert c.control is Control.PENDING_HUMAN


# ── same-session human capture surviving navigation (live browser) ────────────
def test_human_capture_survives_navigation(live_server, tmp_path) -> None:
    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    try:
        capture = HumanCapture(s._context, tmp_path, POLICY)
        capture.start()  # binding + init-script installed BEFORE navigation

        # simulate the human: log in (navigates), then click on the next page
        obs = s.observe()
        u = next(e.index for e in obs.elements if e.role == "textbox" and any("Username" in t for t in e.nearby_text))
        s.act(Action(kind="type", target_index=u, value="teller"))
        obs = s.observe()
        p = next(e.index for e in obs.elements if e.role == "textbox" and any("Password" in t for t in e.nearby_text))
        s.act(Action(kind="type", target_index=p, value="demo-pass-not-secret"))
        obs = s.observe()
        b = next(e.index for e in obs.elements if e.role == "button" and "Sign in" in e.name)
        s.act(Action(kind="click", target_index=b))   # NAVIGATES to /member
        time.sleep(0.2)
        # click on the post-navigation page — listeners must have reattached
        obs = s.observe()
        search = next(e.index for e in obs.elements if e.role == "button" and "Search" in e.name)
        s.act(Action(kind="click", target_index=search))
        time.sleep(0.2)

        actions = capture.actions()
        # captured events from BOTH before and after navigation prove the init-script reattached
        urls = {a["url"] for a in actions}
        assert any("/login" in u for u in urls), f"no pre-nav capture: {urls}"
        assert any("/member" in u for u in urls), f"no post-nav capture (listeners didn't reattach): {urls}"
    finally:
        s.close()


def test_human_capture_redacts_sensitive_values(live_server, tmp_path) -> None:
    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    try:
        capture = HumanCapture(s._context, tmp_path, POLICY, sensitive_values=["demo-pass-not-secret"])
        capture.start()
        obs = s.observe()
        p = next(e.index for e in obs.elements if e.role == "textbox" and any("Password" in t for t in e.nearby_text))
        s.act(Action(kind="type", target_index=p, value="demo-pass-not-secret"))
        # a change event fires with the field value; the capture must scrub it
        s.act(Action(kind="press", value="Tab"))
        time.sleep(0.2)
        text = (Path(tmp_path) / "human_actions.jsonl")
        if text.exists():
            assert "demo-pass-not-secret" not in text.read_text()
    finally:
        s.close()


# ── replay escalation is a REAL handoff (wired into the engine) ───────────────
def test_replay_risky_step_produces_real_intervention(live_server, tmp_path) -> None:
    from lyrebird.capability.store import CapabilityStore
    from lyrebird.replay.executor import ReplayContext, ReplayEngine

    run_dir = tmp_path / "replay-mutating"
    controller = SessionController("replay-mutating", run_dir)

    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    try:
        # login + land on the subaccount form
        obs = s.observe()
        u = next(e.index for e in obs.elements if e.role == "textbox" and any("Username" in t for t in e.nearby_text))
        s.act(Action(kind="type", target_index=u, value="teller"))
        obs = s.observe()
        p = next(e.index for e in obs.elements if e.role == "textbox" and any("Password" in t for t in e.nearby_text))
        s.act(Action(kind="type", target_index=p, value="demo-pass-not-secret"))
        obs = s.observe()
        b = next(e.index for e in obs.elements if e.role == "button" and "Sign in" in e.name)
        s.act(Action(kind="click", target_index=b))
        s.act(Action(kind="navigate", value=f"{live_server}/member/100001/subaccount"))

        cap = CapabilityStore(ROOT / "artifacts").load("open_subaccount", 1)  # draft
        engine = ReplayEngine(cap, s, POLICY, evidence_dir=str(run_dir), controller=controller)
        result = engine.run(ReplayContext(params={"member_id": "100001", "amount": "250.00"}, confirm_risky=True))

        assert result.status == "ESCALATED"
        # the escalation is REAL: run-state flipped and an intervention request was written
        assert RunState.read(run_dir).control is Control.PENDING_HUMAN
        req = InterventionRequest.read(run_dir)
        assert req.capability == "open_subaccount"
        assert "risky" in req.reason.lower()
        assert "/subaccount" in req.url
    finally:
        s.close()


# ── operator CLI ──────────────────────────────────────────────────────────────
def test_operator_cli_list_take_handback(tmp_path, capsys) -> None:
    from lyrebird.handoff.operator_cli import main as op

    run = "discover-x"
    c = SessionController(run, tmp_path / run)
    c.escalate(step=2, reason="needs a human", url="u", screenshot="s", state_digest="d",
               capability="cap", wait=False)

    assert op(["--evidence", str(tmp_path), "list"]) == 0
    out = capsys.readouterr().out
    assert run in out and "PENDING_HUMAN" in out and "needs a human" in out

    assert op(["--evidence", str(tmp_path), "take", run, "--as", "carol"]) == 0
    assert RunState.read(tmp_path / run).control is Control.HUMAN

    assert op(["--evidence", str(tmp_path), "handback", run, "--note", "done"]) == 0
    assert RunState.read(tmp_path / run).control is Control.AUTOMATION
