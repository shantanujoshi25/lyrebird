"""ReplayEngine — deterministic execution of a Capability. No LLM.

Per step: resolve the target locator, policy-check the action, act, wait for the page to
settle, then detect known conditions. The taxonomy (A8) drives what happens next:

  business_outcome -> return a ReplayResult(status=BUSINESS_OUTCOME, outcome_code=...)
  recoverable      -> dismiss / relogin / retry-backoff, then continue (a handled condition
                      does NOT turn a SUCCESS into a failure)
  hard             -> FAILURE (ESCALATED when a human is in the loop, wired in C7)

After the steps, the success checkpoint is verified and declared outputs are extracted. Every
resolution's fallback depth is recorded (drift telemetry). Params are substituted from the
caller-supplied values; sensitive params come from env, never the artifact.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from lyrebird.capability.schema import (
    Capability, ConditionDetector, KnownCondition, OutcomeClass, OutcomeCode, ReplayResult, Step,
)
from lyrebird.policy import Policy, check_action, classify_risk
from lyrebird.capability.schema import Risk
from lyrebird.replay.detector import detected
from lyrebird.replay.resolver import ResolveError, resolve
from lyrebird.surface.base import Action, Observation, Surface, Viewport

# Recoverable-condition retry budget defaults (a step's KnownCondition.max_retries overrides).
_BACKOFF_S = 0.2


@dataclass
class ReplayContext:
    params: dict[str, str]                 # per-invocation input values (member_id=...)
    confirm_risky: bool = False            # caller opt-in for risky steps on approved artifacts
    relogin: Callable[[], None] | None = None  # subflow to re-authenticate on SESSION_EXPIRED
    max_transient_retries: int = 2         # TRANSIENT/APP_ERROR budget before degrading to hard


class ReplayEngine:
    def __init__(self, cap: Capability, surface: Surface, policy: Policy, *, evidence_dir: str = "") -> None:
        self.cap = cap
        self.surface = surface
        self.policy = policy
        self.evidence_dir = evidence_dir
        self._recorded_vp = cap.provenance.viewport

    def run(self, ctx: ReplayContext) -> ReplayResult:
        fallback_depths: dict[int, int] = {}

        for step in self.cap.steps:
            # 1. risky-step gate (R4): block unless approved + confirm_risky, else escalate.
            if classify_risk(self.policy, step) is Risk.RISKY:
                if not (self.cap.status == "approved" and ctx.confirm_risky):
                    return self._result("ESCALATED", outcome_code=None, failed_step=step.index,
                                        expected="approved artifact + confirm_risky", observed=f"status={self.cap.status}",
                                        fallback_depths=fallback_depths)

            outcome = self._run_step(step, ctx, fallback_depths)
            if outcome is not None:
                return outcome  # a business outcome or a hard failure short-circuits

        # 2. success checkpoint
        obs, text = self._perceive()
        if not detected(self.cap.success_checkpoint, obs, text):
            return self._result("FAILURE", outcome_code=OutcomeCode.CHECKPOINT_FAILED,
                                expected=self.cap.success_checkpoint.match, observed="checkpoint not present",
                                fallback_depths=fallback_depths)

        # 3. extract declared outputs
        outputs = self._extract_outputs(text)
        return self._result("SUCCESS", outputs=outputs, fallback_depths=fallback_depths)

    # ── per-step execution ────────────────────────────────────────────────
    def _run_step(self, step: Step, ctx: ReplayContext, fallback_depths: dict[int, int]) -> ReplayResult | None:
        obs, _ = self._perceive()

        # resolve target (if any) against the current observation
        target_index: int | None = None
        if step.target is not None:
            try:
                res = resolve(step.target, obs, recorded_viewport=self._recorded_vp, live_viewport=self.surface.viewport)
            except ResolveError as exc:
                return self._result("FAILURE", outcome_code=OutcomeCode.LOCATOR_NOT_FOUND,
                                    failed_step=step.index, expected=step.target.semantic_id, observed=str(exc),
                                    fallback_depths=fallback_depths)
            target_index = res.index
            fallback_depths[step.index] = res.fallback_depth

        # policy check BEFORE acting (R4)
        action_type = step.action
        url = self.surface.observe().url
        decision = check_action(self.policy, url=url, action_type=action_type)
        if not decision.allowed:
            return self._result("ESCALATED", outcome_code=None, failed_step=step.index,
                                expected="allowed action", observed=decision.reason, fallback_depths=fallback_depths)

        # substitute params into the value
        value = self._substitute(step.value, ctx)

        # act, with recoverable-condition handling around it
        return self._act_with_recovery(step, target_index, value, ctx, fallback_depths)

    def _act_with_recovery(self, step, target_index, value, ctx, fallback_depths) -> ReplayResult | None:
        attempts = 0
        while True:
            self.surface.act(Action(kind=step.action, target_index=target_index, value=value))
            obs, text = self._perceive()

            hit = self._match_condition(obs, text)
            if hit is None:
                return None  # clean step, continue to next

            if hit.klass is OutcomeClass.BUSINESS_OUTCOME:
                return self._result("BUSINESS_OUTCOME", outcome_code=hit.code, failed_step=step.index,
                                    fallback_depths=fallback_depths)

            if hit.klass is OutcomeClass.RECOVERABLE:
                attempts += 1
                if attempts > max(hit.max_retries, ctx.max_transient_retries):
                    # recovery budget exhausted -> the recoverable condition degrades to hard.
                    return self._result("FAILURE", outcome_code=hit.code, failed_step=step.index,
                                        expected="condition cleared", observed="recovery budget exhausted",
                                        fallback_depths=fallback_depths)
                if not self._recover(hit, obs, ctx):
                    return self._result("FAILURE", outcome_code=hit.code, failed_step=step.index,
                                        expected="recoverable handled", observed="could not recover",
                                        fallback_depths=fallback_depths)

                # `dismiss` removes a client-side overlay in place — the underlying page is
                # already correct, so just RE-CHECK conditions without redoing the action
                # (re-navigating would re-show the same modal). `relogin`/`retry_backoff` DO
                # need the action re-attempted, because recovery changed the page away from the
                # intended state (back to /login, or a transient error page).
                if hit.on_detect == "dismiss":
                    obs, text = self._perceive()
                    if self._match_condition(obs, text) is None:
                        return None  # overlay gone, underlying page clean -> step done
                    continue         # something else surfaced; loop will handle or exhaust
                time.sleep(_BACKOFF_S * attempts if hit.on_detect == "retry_backoff" else 0)
                if step.target is not None:  # re-resolve after a page change (indices shift)
                    obs2, _ = self._perceive()
                    try:
                        res = resolve(step.target, obs2, recorded_viewport=self._recorded_vp, live_viewport=self.surface.viewport)
                        target_index = res.index
                        fallback_depths[step.index] = res.fallback_depth
                    except ResolveError:
                        pass
                continue

            # hard
            return self._result("FAILURE", outcome_code=hit.code, failed_step=step.index,
                                fallback_depths=fallback_depths)

    # ── condition handling ────────────────────────────────────────────────
    def _match_condition(self, obs: Observation, text: str) -> KnownCondition | None:
        for cond in self.cap.known_conditions:
            if detected(cond.detector, obs, text):
                return cond
        return None

    def _recover(self, cond: KnownCondition, obs: Observation, ctx: ReplayContext) -> bool:
        if cond.on_detect == "dismiss":
            # find and click a dismiss control (button whose name implies dismissal)
            for e in obs.elements:
                if e.role == "button" and re.search(r"dismiss|close|ok", e.name, re.I):
                    self.surface.act(Action(kind="click", target_index=e.index))
                    return True
            return False
        if cond.on_detect == "relogin":
            if ctx.relogin is not None:
                ctx.relogin()
                return True
            return False
        if cond.on_detect == "retry_backoff":
            return True  # the loop handles the actual backoff/re-act
        return False

    # ── outputs / perception / result ─────────────────────────────────────
    def _extract_outputs(self, text: str) -> dict[str, object]:
        out: dict[str, object] = {}
        for spec in self.cap.outputs:
            label = spec.extract.candidates[0].args.get("label", "")
            # find "<label> ... $<number>" in the visible text
            m = re.search(rf"{re.escape(label)}[^\d$]*\$?([\d,]+\.\d{{2}})", text)
            if m:
                raw = m.group(1).replace(",", "")
                out[spec.name] = float(raw) if spec.transform in ("number", "currency") else raw
        return out

    def _substitute(self, value: str | None, ctx: ReplayContext) -> str | None:
        if not value:
            return value
        m = re.fullmatch(r"\{\{(\w+)\}\}", value)
        if not m:
            return value
        name = m.group(1)
        param = next((p for p in self.cap.inputs if p.name == name), None)
        if param and param.sensitive:
            return os.environ.get(f"LYREBIRD_PARAM_{name.upper()}", "")
        return ctx.params.get(name, "")

    def _perceive(self) -> tuple[Observation, str]:
        return self.surface.observe(), self.surface.page_text()

    def _result(self, status, *, outcome_code=None, outputs=None, failed_step=None, expected=None,
                observed=None, fallback_depths=None) -> ReplayResult:
        return ReplayResult(
            status=status, outcome_code=outcome_code, outputs=outputs or {},
            failed_step=failed_step, expected=expected, observed=observed,
            fallback_depths=fallback_depths or {}, evidence_dir=self.evidence_dir,
        )
