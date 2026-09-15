"""The intervention request — the context a human needs to act (R6).

When a run gets stuck, the engine writes `intervention_request.json` carrying enough for an
operator to understand and act: which capability/goal, the current step, the current state
(URL + screenshot reference + a text digest), and WHY it stopped. Written to the run's
evidence dir alongside the run-state file.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class InterventionRequest(BaseModel):
    run_id: str
    capability: str          # capability id / goal being pursued
    step: int                # the step index we stopped at
    url: str                 # where the session currently is
    reason: str              # why we stopped (stuck trigger / hard condition / risky step)
    screenshot: str          # relative path to the (masked) screenshot in the run dir
    state_digest: str        # short text digest of the current screen (redacted)

    @staticmethod
    def path(run_dir: Path | str) -> Path:
        return Path(run_dir) / "intervention_request.json"

    def write(self, run_dir: Path | str) -> Path:
        p = self.path(run_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2))
        return p

    @classmethod
    def read(cls, run_dir: Path | str) -> "InterventionRequest":
        return cls.model_validate_json(cls.path(run_dir).read_text())
