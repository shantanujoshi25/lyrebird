"""Handoff control-transfer — the state machine + operator round-trip. No browser, no LLM.

Proves the mechanism the brief calls out as "real, not a TODO": the engine escalates to
PENDING_HUMAN with an intervention request, an operator takes control of the (same) run and
hands it back, and the run-state file — the cross-process channel — reflects each transition.
Illegal transitions are rejected.
"""

from __future__ import annotations

import pytest

from lyrebird.handoff.controller import SessionController
from lyrebird.handoff.runstate import Control, RunState


def test_escalate_take_handback_round_trip(tmp_path) -> None:
    c = SessionController("run1", tmp_path)
    assert c.control is Control.AUTOMATION

    # engine escalates (non-blocking): writes the request + flips to PENDING_HUMAN
    c.escalate(step=5, reason="risky step requires human authorization", url="http://x/review",
               screenshot="", state_digest="abc", capability="open_subaccount", wait=False)
    assert RunState.read(tmp_path).control is Control.PENDING_HUMAN
    assert (tmp_path / "intervention_request.json").exists()

    # operator (separate process, same files) takes control, then hands back
    assert SessionController.operator_take(tmp_path, "alice").control is Control.HUMAN
    assert SessionController.operator_handback(tmp_path, "authorized manually").control is Control.AUTOMATION


def test_illegal_transition_is_rejected(tmp_path) -> None:
    c = SessionController("run2", tmp_path)
    with pytest.raises(Exception):  # AUTOMATION -> HUMAN is not a legal edge (must go via PENDING_HUMAN)
        c._transition(Control.HUMAN)
