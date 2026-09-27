"""SessionController — the handoff state machine (ARCH §5).

Enforces the legal transitions and owns the run-state file. The engine (discovery or replay)
calls `escalate(...)` when stuck; that writes the intervention request, flips the run-state to
PENDING_HUMAN, and blocks by POLLING the file until an operator hands control back (or a
timeout). On handback the controller re-verifies a caller-supplied checkpoint before
returning control to automation — so the run only resumes if the human actually reached the
expected state. Fires identically in both loops (A3b).

    AUTOMATION --escalate--> PENDING_HUMAN --(operator take)--> HUMAN
    HUMAN --(operator handback)--> AUTOMATION   (checkpoint re-verified)
    any --> DONE
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from lyrebird.handoff.intervention import InterventionRequest
from lyrebird.handoff.runstate import Control, RunState

# legal transitions — anything else is a programming error
_LEGAL: dict[Control, set[Control]] = {
    Control.NONE: {Control.AUTOMATION},
    Control.AUTOMATION: {Control.PENDING_HUMAN, Control.DONE},
    Control.PENDING_HUMAN: {Control.HUMAN, Control.DONE},
    Control.HUMAN: {Control.AUTOMATION, Control.DONE},
    Control.DONE: set(),
}


class IllegalTransition(Exception):
    pass


class SessionController:
    def __init__(self, run_id: str, run_dir: Path | str) -> None:
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.state = RunState(run_id=run_id, control=Control.AUTOMATION)
        self.state.write(self.run_dir)

    # ── transitions ────────────────────────────────────────────────────────
    def _transition(self, to: Control, **fields) -> None:
        if to not in _LEGAL[self.state.control]:
            raise IllegalTransition(f"{self.state.control} -> {to} is not allowed")
        self.state = self.state.model_copy(update={"control": to, **fields})
        self.state.write(self.run_dir)

    @property
    def control(self) -> Control:
        # always answer from the file — an operator process may have changed it
        self.state = RunState.read(self.run_dir)
        return self.state.control

    def escalate(
        self,
        *,
        step: int,
        reason: str,
        url: str,
        screenshot: str,
        state_digest: str,
        capability: str,
        wait: bool = True,
        timeout_s: float = 300.0,
        on_resume_checkpoint: Callable[[], bool] | None = None,
    ) -> Control:
        """Write the intervention request, flip to PENDING_HUMAN, and (optionally) block until
        an operator hands control back — re-verifying the checkpoint before resuming."""
        InterventionRequest(
            run_id=self.run_id, capability=capability, step=step, url=url,
            reason=reason, screenshot=screenshot, state_digest=state_digest,
        ).write(self.run_dir)
        self._transition(Control.PENDING_HUMAN, current_step=step, reason=reason)

        if not wait:
            return Control.PENDING_HUMAN

        # block by POLLING the file until the operator hands back (HUMAN done -> AUTOMATION),
        # or timeout. The operator's `take` sets HUMAN, `handback` sets AUTOMATION.
        final = RunState.poll_until(self.run_dir, Control.AUTOMATION, timeout_s=timeout_s)
        self.state = final
        if final.control != Control.AUTOMATION:
            return final.control  # timed out still waiting

        # resume gate: re-verify the checkpoint the human was supposed to reach.
        if on_resume_checkpoint is not None and not on_resume_checkpoint():
            self._transition(Control.PENDING_HUMAN, reason="handback checkpoint re-verification failed")
            return Control.PENDING_HUMAN
        return Control.AUTOMATION

    def finish(self, reason: str = "") -> None:
        self._transition(Control.DONE, reason=reason)

    # ── operator-side transitions (called from the operator CLI, another process) ──
    @staticmethod
    def operator_take(run_dir: Path | str, operator: str) -> RunState:
        state = RunState.read(run_dir)
        if state.control is not Control.PENDING_HUMAN:
            raise IllegalTransition(f"cannot take control from {state.control}")
        state = state.model_copy(update={"control": Control.HUMAN, "operator": operator})
        state.write(run_dir)
        return state

    @staticmethod
    def operator_handback(run_dir: Path | str, note: str = "") -> RunState:
        state = RunState.read(run_dir)
        if state.control is not Control.HUMAN:
            raise IllegalTransition(f"cannot hand back from {state.control}")
        state = state.model_copy(update={"control": Control.AUTOMATION, "reason": note or "handback"})
        state.write(run_dir)
        return state
