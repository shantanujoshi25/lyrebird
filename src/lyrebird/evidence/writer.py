"""EvidenceWriter — writes a run directory with redaction on the write path.

Layout per run (docs/01_ARCHITECTURE.md §9):
    evidence/<run_id>/
      steps.jsonl        one line per step: observation summary, reasoning, action, result
      screenshots/       per-step PNG, sensitive-field bboxes masked before write
      aria/              per-step element-list snapshot
      run_state.json     handoff state (written by the SessionController in C7)
      intervention_request.json / human_actions.jsonl   (C7)
      result.json        artifact pointer (discovery) / ReplayResult (replay)

Redaction is applied HERE, at write time: every string that lands in steps.jsonl is passed
through `redact_text`, and every screenshot is passed through `mask_screenshot`. So there is
no window in which an unredacted value hits disk — the sink is the last line of defense.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lyrebird.policy import Policy, mask_screenshot, redact_text


class EvidenceWriter:
    def __init__(
        self,
        run_id: str,
        policy: Policy,
        *,
        root: Path | str = "evidence",
        sensitive_values: list[str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.policy = policy
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "screenshots").mkdir(exist_ok=True)
        (self.dir / "aria").mkdir(exist_ok=True)
        self._steps_path = self.dir / "steps.jsonl"
        # Sensitive literal values (e.g. the password read from env) scrubbed from all text.
        self._sensitive_values = list(sensitive_values or [])

    def _scrub(self, obj: Any) -> Any:
        """Recursively redact every string in a JSON-able structure."""
        if isinstance(obj, str):
            return redact_text(self.policy, obj, sensitive_values=self._sensitive_values)
        if isinstance(obj, dict):
            return {k: self._scrub(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._scrub(v) for v in obj]
        return obj

    def log_step(self, record: dict[str, Any]) -> None:
        """Append one redacted step record to steps.jsonl."""
        with self._steps_path.open("a") as fh:
            fh.write(json.dumps(self._scrub(record)) + "\n")

    def save_screenshot(self, step_index: int, png: bytes, *, mask_boxes: list[tuple[int, int, int, int]] | None = None) -> Path:
        """Write a per-step screenshot with sensitive-field bboxes masked before write."""
        raw = self.dir / "screenshots" / f"step-{step_index:02d}.raw.png"
        raw.write_bytes(png)
        out = self.dir / "screenshots" / f"step-{step_index:02d}.png"
        mask_screenshot(raw, boxes=mask_boxes or [], out_path=out)
        raw.unlink(missing_ok=True)  # never leave the un-masked original on disk
        return out

    def save_aria(self, step_index: int, elements: list[dict[str, Any]]) -> Path:
        path = self.dir / "aria" / f"step-{step_index:02d}.json"
        path.write_text(json.dumps(self._scrub(elements), indent=2))
        return path

    def write_result(self, result: dict[str, Any]) -> Path:
        path = self.dir / "result.json"
        path.write_text(json.dumps(self._scrub(result), indent=2))
        return path
