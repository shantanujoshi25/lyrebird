"""Drive a replay and write a full evidence run — no LLM.

Glue for `lyrebird replay` and for generating the committed evidence (C8). It loads a
capability, logs in, drives the engine, writes redacted per-step screenshots/ARIA + the
final ReplayResult to an evidence run dir, and (for the mutating capability) wires the
SessionController so a gated risky step becomes a real handoff.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from lyrebird.capability.store import CapabilityStore
from lyrebird.evidence import EvidenceWriter
from lyrebird.handoff.controller import SessionController
from lyrebird.policy import Policy
from lyrebird.replay.executor import ReplayContext, ReplayEngine
from lyrebird.surface.base import Action
from lyrebird.surface.playwright_web import PlaywrightWebSurface

ROOT = Path(__file__).resolve().parents[3]
POLICY_YAML = ROOT / "policy.yaml"


def _login(surface: PlaywrightWebSurface, user: str, password: str) -> None:
    obs = surface.observe()
    u = next(e.index for e in obs.elements if e.role == "textbox" and any("Username" in t for t in e.nearby_text))
    surface.act(Action(kind="type", target_index=u, value=user))
    obs = surface.observe()
    p = next(e.index for e in obs.elements if e.role == "textbox" and any("Password" in t for t in e.nearby_text))
    surface.act(Action(kind="type", target_index=p, value=password))
    obs = surface.observe()
    b = next(e.index for e in obs.elements if e.role == "button" and "Sign in" in e.name)
    surface.act(Action(kind="click", target_index=b))


def replay_run(
    capability_id: str,
    base_url: str,
    *,
    params: dict[str, str],
    version: int = 1,
    status_override: str | None = None,
    confirm_risky: bool = False,
    pre_login: bool = False,
    pre_nav: str | None = None,
    headed: bool = False,
    sensitive_values: list[str] | None = None,
) -> tuple[str, str]:
    """Run a replay end-to-end, writing evidence. Returns (status, evidence_dir)."""
    run_id = f"replay-{capability_id}-{_dt.datetime.now():%Y%m%d-%H%M%S}"
    policy = Policy.load(POLICY_YAML)
    cap = CapabilityStore(ROOT / "artifacts").load(capability_id, version)
    if status_override:
        cap = cap.model_copy(update={"status": status_override})

    ev = EvidenceWriter(run_id, policy, sensitive_values=sensitive_values)
    controller = SessionController(run_id, ev.dir)  # real handoff on escalation

    surface = PlaywrightWebSurface(f"{base_url}/login", headed=headed)
    try:
        # The capability drives from the start URL. If it does NOT include login steps (e.g.
        # the mutating capability starts mid-flow), `pre_login` + `pre_nav` set the stage.
        if pre_login:
            _login(surface, os.environ.get("MOCK_USERNAME", "teller"), os.environ.get("MOCK_PASSWORD", "demo-pass-not-secret"))
        if pre_nav:
            surface.act(Action(kind="navigate", value=f"{base_url}{pre_nav}"))

        def on_step(i: int, status: str) -> None:
            obs = surface.observe()
            ev.save_screenshot(i, obs.screenshot_png)
            ev.save_aria(i, [e.model_dump() for e in obs.elements])
            ev.log_step({"step": i, "status": status, "url": obs.url})

        engine = ReplayEngine(cap, surface, policy, evidence_dir=str(ev.dir), controller=controller, on_step=on_step)
        result = engine.run(ReplayContext(params=params, confirm_risky=confirm_risky))
        ev.write_result(result.model_dump())
        if result.status != "ESCALATED":
            controller.finish(result.status)
        print(f"[replay] {capability_id}: {result.status} outcome={result.outcome_code} "
              f"outputs={result.outputs} evidence={ev.dir}")
        return result.status, str(ev.dir)
    finally:
        surface.close()
