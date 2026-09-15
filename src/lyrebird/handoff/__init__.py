"""handoff/ — the real control-transfer model.

Automation must be able to pause, cede control of the SAME live session to a human, and
resume — with a clear answer to "who is in control" at all times (R6). The control channel
is a run-state FILE on disk, not an in-memory lock, because the operator CLI runs in a
separate process (A3): the engine polls the file; the operator writes to it. See
docs/01_ARCHITECTURE.md §5.

    runstate.py     the cross-process run-state file (State enum + read/write/poll)
    controller.py   the SessionController state machine + transition guards
    intervention.py the intervention request written on escalation (context for the human)
    capture.py      same-session human-action capture (add_init_script + expose_binding)
    operator_cli.py the mock operator console: list / take / handback
"""

from lyrebird.handoff.controller import SessionController
from lyrebird.handoff.intervention import InterventionRequest
from lyrebird.handoff.runstate import Control, RunState

__all__ = ["Control", "InterventionRequest", "RunState", "SessionController"]
