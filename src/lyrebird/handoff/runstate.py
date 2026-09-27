"""The run-state file — the cross-process control channel (A3).

`run_state.json` in the run's evidence dir is the single source of truth for who holds
control. The engine writes it and polls it; the operator CLI (a separate process) writes it
to take/hand back control. Because it's a file, "who is in control" is always answerable by
anyone who can read the run dir — there is no in-memory lock a second process couldn't see.

Writes are atomic (write-temp-then-rename) so a reader never sees a half-written file.
"""

from __future__ import annotations

import os
import time
from enum import Enum
from pathlib import Path

from pydantic import BaseModel


class Control(str, Enum):
    """Who is (or should be) in control — the answer R6 requires at all times."""

    NONE = "NONE"
    AUTOMATION = "AUTOMATION"      # the engine is driving
    PENDING_HUMAN = "PENDING_HUMAN"  # engine stopped, waiting for a human to take control
    HUMAN = "HUMAN"               # a human operator is driving the same live session
    DONE = "DONE"                 # run finished (success, escalation abort, or completion)


class RunState(BaseModel):
    run_id: str
    control: Control = Control.AUTOMATION
    current_step: int = 0
    reason: str = ""              # why we're in the current state (e.g. stuck trigger)
    operator: str | None = None   # who took control, when HUMAN
    updated_at: float = 0.0

    # ── file I/O (atomic) ──────────────────────────────────────────────────
    @staticmethod
    def path(run_dir: Path | str) -> Path:
        return Path(run_dir) / "run_state.json"

    def write(self, run_dir: Path | str) -> None:
        self.updated_at = time.time()
        p = self.path(run_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(self.model_dump_json(indent=2))
        os.replace(tmp, p)  # atomic rename — readers never see a partial file

    @classmethod
    def read(cls, run_dir: Path | str) -> RunState:
        return cls.model_validate_json(cls.path(run_dir).read_text())

    @classmethod
    def poll_until(cls, run_dir: Path | str, target: Control, *, timeout_s: float, interval_s: float = 0.1) -> RunState:
        """Block (by polling the file) until control reaches `target` or timeout.

        This is how the engine 'blocks' during PENDING_HUMAN: it does not hold an in-memory
        lock — it re-reads the file every interval so the operator process's write is seen.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            state = cls.read(run_dir)
            if state.control == target:
                return state
            time.sleep(interval_s)
        return cls.read(run_dir)  # return last-known state on timeout; caller decides
