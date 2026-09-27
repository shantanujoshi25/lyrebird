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

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from lyrebird.capability.schema import (
    Capability,
    KnownCondition,
    OutcomeClass,
    OutcomeCode,
    ReplayResult,
    Risk,
    Step,
)
from lyrebird.policy import Policy, check_action, classify_risk
from lyrebird.replay.detector import detected
from lyrebird.surface.base import Action, Observation, Surface

# Recoverable-condition retry budget defaults (a step's KnownCondition.max_retries overrides).
_BACKOFF_S = 0.2

# Value-shape patterns for tag-agnostic anchor resolution (R2). A "currency"/"number" value is
# matched wherever it appears near the recorded label; "text" grabs a short trailing token.
def _coerce_by_type(raw_text: str, declared_type: str) -> object:
    """Coerce the extracted string per the output's DECLARED type (from the artifact), not by
    guessing the shape. number/integer -> pull the numeric part and float/int it; else the string.
    The LLM already produced the right value; this only casts it to the caller's declared type."""
    if declared_type in ("number", "integer"):
        m = re.search(r"-?\d[\d,]*(?:\.\d+)?", raw_text)
        if m:
            num = float(m.group(0).replace(",", ""))
            return int(num) if declared_type == "integer" else num
    return raw_text.strip()


@dataclass
class ReplayContext:
    params: dict[str, str]                 # per-invocation NON-secret input values (member_id=...)
    secrets: dict[str, str] | None = None  # sensitive input values, from the local secrets file (A7)
    confirm_risky: bool = False            # caller opt-in for risky steps on approved artifacts
    relogin: Callable[[], None] | None = None  # subflow to re-authenticate on SESSION_EXPIRED
    max_transient_retries: int = 2         # TRANSIENT/APP_ERROR budget before degrading to hard


class ReplayEngine:
    def __init__(self, cap: Capability, surface: Surface, policy: Policy, *, evidence_dir: str = "",
                 controller: object | None = None, on_step: Callable[[int, str], None] | None = None,
                 deviation_handler: Callable | None = None) -> None:
        self.cap = cap
        self.surface = surface
        self.policy = policy
        self.evidence_dir = evidence_dir
        self.controller = controller  # optional SessionController: makes escalation a REAL handoff
        self.on_step = on_step        # optional evidence hook: (step_index, status) -> writes a snapshot
        # optional R4 seam: on a genuine deviation, produce an LLM diagnostic (text only) + hand
        # off to a human. Signature: (step, expected, observed, obs, text, fallback_depths) -> ReplayResult.
        # Default None keeps replay fully deterministic (deviation -> structured FAILURE).
        self._deviation_handler = deviation_handler

    def _emit_step(self, step_index: int, status: str) -> None:
        if self.on_step is not None:
            try:
                self.on_step(step_index, status)
            except Exception:
                pass  # evidence writing must never break a replay

    def run(self, ctx: ReplayContext) -> ReplayResult:
        fallback_depths: dict[int, int] = {}

        for step in self.cap.steps:
            # 1. risky-step gate (R4): block unless approved + confirm_risky, else escalate.
            if classify_risk(self.policy, step) is Risk.RISKY:
                if not (self.cap.status == "approved" and ctx.confirm_risky):
                    self._emit_step(step.index, "risky-gate")
                    self._escalate(step.index, "risky step requires human authorization")
                    return self._result("ESCALATED", outcome_code=None, failed_step=step.index,
                                        expected="approved artifact + confirm_risky", observed=f"status={self.cap.status}",
                                        fallback_depths=fallback_depths)

            outcome = self._run_step(step, ctx, fallback_depths)
            if outcome is not None:
                self._emit_step(step.index, outcome.status)
                return outcome  # a business outcome or a hard failure short-circuits

            # SUPERVISE (R3): if this step recorded a verified postcondition, confirm it holds —
            # deterministically, NO LLM. If it doesn't, re-check known conditions; if still
            # unexplained, this is a DEVIATION (R4 routes it to diagnostic + human handoff).
            if step.postcondition is not None:
                obs, text = self._perceive()
                # substitute {{param}} in the expected text with this invocation's value, so a
                # postcondition that references a bound input (e.g. the member id) checks the
                # RIGHT value on replay rather than the discovery-time constant.
                pc = step.postcondition.model_copy(update={"match": self._substitute(step.postcondition.match, ctx)})
                if not detected(pc, obs, text):
                    known = self._match_condition(obs, text)
                    if known is not None:
                        # a known condition explains it — handle via the taxonomy path
                        dev = self._handle_condition(known, step, ctx, fallback_depths)
                        if dev is not None:
                            self._emit_step(step.index, dev.status)
                            return dev
                    else:
                        self._emit_step(step.index, "DEVIATION")
                        deviation = self._on_deviation(step, obs, text, fallback_depths)
                        return deviation
            self._emit_step(step.index, "ok")

        # 2. success checkpoint
        obs, text = self._perceive()
        if not detected(self.cap.success_checkpoint, obs, text):
            return self._result("FAILURE", outcome_code=OutcomeCode.CHECKPOINT_FAILED,
                                expected=self.cap.success_checkpoint.match, observed="checkpoint not present",
                                fallback_depths=fallback_depths)

        # 3. extract declared outputs. A failed extraction self-check (the recipe returned a value
        #    inconsistent with what the LLM saw at discovery) is a DEVIATION, not a silent bad value.
        outputs, deviation = self._extract_outputs(obs, text)
        if deviation is not None:
            name, reason = deviation
            last = self.cap.steps[-1] if self.cap.steps else None
            if last is not None:
                return self._on_deviation(last, obs, f"output {name}: {reason}", fallback_depths)
            return self._result("FAILURE", outcome_code=OutcomeCode.CHECKPOINT_FAILED,
                                expected=f"valid {name}", observed=reason, fallback_depths=fallback_depths)
        return self._result("SUCCESS", outputs=outputs, fallback_depths=fallback_depths)

    # ── per-step execution ────────────────────────────────────────────────
    def _run_step(self, step: Step, ctx: ReplayContext, fallback_depths: dict[int, int]) -> ReplayResult | None:
        # policy check BEFORE acting (R4). For navigate, check the TARGET url (where we're
        # going), not the current page — consistent with the discovery loop; otherwise a
        # navigate to a disallowed domain would be judged against the (allowed) current page.
        action_type = step.action
        url = step.value if (step.action == "navigate" and step.value) else self.surface.observe().url
        decision = check_action(self.policy, url=url, action_type=action_type)
        if not decision.allowed:
            return self._result("ESCALATED", outcome_code=None, failed_step=step.index,
                                expected="allowed action", observed=decision.reason, fallback_depths=fallback_depths)

        # substitute params into the value
        value = self._substitute(step.value, ctx)

        # act, with recoverable-condition handling around it
        return self._act_with_recovery(step, value, ctx, fallback_depths)

    def _act_with_recovery(self, step, value, ctx, fallback_depths) -> ReplayResult | None:
        attempts = 0
        while True:
            # Resolve+act via the durable locator directly (no element index). The Surface tries
            # the recorded candidates in order; the winning index is the drift signal. A target
            # that resolves to nothing is a hard LOCATOR_NOT_FOUND.
            if step.target is not None:
                depth = self._act_locator(step, value, fallback_depths)
                if depth < 0:
                    return self._result("FAILURE", outcome_code=OutcomeCode.LOCATOR_NOT_FOUND,
                                        failed_step=step.index, expected=step.target.semantic_id,
                                        observed="no recorded locator resolved", fallback_depths=fallback_depths)
            else:
                self.surface.act(Action(kind=step.action, value=value))  # navigate / global press
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
                continue  # loop re-resolves+re-acts the durable locator on the fresh page

            # hard
            return self._result("FAILURE", outcome_code=hit.code, failed_step=step.index,
                                fallback_depths=fallback_depths)

    def _act_locator(self, step: Step, value: str | None, fallback_depths: dict[int, int]) -> int:
        """Resolve the step's durable locator and act on it. Returns the winning candidate depth
        (drift signal), or -1 if nothing resolved. Records depth into fallback_depths."""
        pw, depth = self.surface.resolve_locator(step.target)  # type: ignore[attr-defined]
        if pw is None:
            return -1
        fallback_depths[step.index] = depth
        self.surface.act_locator(step.action, step.target, value)
        return depth

    # ── supervision (R3): postcondition failed → classify or deviate ──────
    def _handle_condition(self, cond: KnownCondition, step: Step, ctx: ReplayContext,
                          fallback_depths: dict[int, int]) -> ReplayResult | None:
        """A known condition explains a failed postcondition. Business/hard are terminal;
        recoverable returns None so the run continues (the checkpoint/next step will catch it)."""
        if cond.klass is OutcomeClass.BUSINESS_OUTCOME:
            return self._result("BUSINESS_OUTCOME", outcome_code=cond.code, failed_step=step.index,
                                fallback_depths=fallback_depths)
        if cond.klass is OutcomeClass.HARD:
            return self._result("FAILURE", outcome_code=cond.code, failed_step=step.index,
                                fallback_depths=fallback_depths)
        return None  # recoverable — let the flow continue

    def _on_deviation(self, step: Step, obs: Observation, text: str,
                      fallback_depths: dict[int, int]) -> ReplayResult:
        """A step's verified postcondition failed and NO known condition explains it — a genuine
        deviation. R3: return a structured FAILURE (deviation) with expected vs observed. R4
        upgrades this to: generate an LLM diagnostic (text only) + hand off to a human + log for
        offline artifact update. The deviation hook is a single seam (`self._deviation_handler`)."""
        expected = step.postcondition.match if step.postcondition else ""
        observed = (text or "")[:200]
        if self._deviation_handler is not None:
            return self._deviation_handler(step, expected, observed, obs, text, fallback_depths)
        return self._result("FAILURE", outcome_code=OutcomeCode.CHECKPOINT_FAILED, failed_step=step.index,
                            expected=expected, observed=f"deviation: postcondition not met (saw: {observed!r})",
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
                if e.role == "button" and re.search(r"dismiss|close|ok", e.name, re.IGNORECASE):
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
    def _extract_outputs(self, obs: Observation, text: str) -> tuple[dict[str, object], tuple[str, str] | None]:
        """Read each declared output by RESOLVING its durable locator against the live page (the
        Surface tries the recorded candidates in order — a stable label anchor first for a variable
        value, so a different member's balance still resolves). Returns (outputs, deviation): a
        deviation is (name, reason) when a locator resolves to nothing — routed to the handoff path.
        The only per-type logic is casting the read TEXT to the output's declared type."""
        out: dict[str, object] = {}
        for spec in self.cap.outputs:
            if spec.locator is None:
                continue
            raw_text, _ = self.surface.read_locator(spec.locator)
            if raw_text is None:
                return out, (spec.name, f"output locator {spec.locator.semantic_id!r} did not resolve")
            if not raw_text.strip():
                return out, (spec.name, f"output {spec.name!r} resolved but was empty")
            out[spec.name] = _coerce_by_type(raw_text, spec.type)
        return out, None

    def _param_value(self, name: str, ctx: ReplayContext) -> str:
        param = next((p for p in self.cap.inputs if p.name == name), None)
        if param and param.sensitive:  # secrets never come from the artifact/ctx.params (A7)
            return (ctx.secrets or {}).get(name, "")
        return ctx.params.get(name, "")

    def _substitute(self, value: str | None, ctx: ReplayContext) -> str | None:
        if not value:
            return value
        # whole-string param (a step's typed value) -> exact value (preserves type/format)
        m = re.fullmatch(r"\{\{(\w+)\}\}", value)
        if m:
            return self._param_value(m.group(1), ctx)
        # embedded params (e.g. a postcondition "Member {{member_id}}") -> substring replace
        return re.sub(r"\{\{(\w+)\}\}", lambda mm: self._param_value(mm.group(1), ctx), value)

    def _escalate(self, step: int, reason: str) -> None:
        """If a SessionController is attached, turn this stop into a REAL handoff: write the
        intervention request + flip the run-state to PENDING_HUMAN. Unattended replay does not
        block (wait=False) — an operator picks the run up out of band via the operator CLI."""
        if self.controller is None:
            return
        try:
            obs = self.surface.observe()
            self.controller.escalate(
                step=step, reason=reason, url=obs.url,
                screenshot=f"screenshots/step-{step:02d}.png",
                state_digest=f"{obs.title} @ {obs.url}",
                capability=self.cap.capability_id, wait=False,
            )
        except Exception:
            pass  # escalation is best-effort telemetry; never mask the underlying result

    def _perceive(self) -> tuple[Observation, str]:
        return self.surface.observe(), self.surface.page_text()

    def _result(self, status, *, outcome_code=None, outputs=None, failed_step=None, expected=None,
                observed=None, fallback_depths=None) -> ReplayResult:
        return ReplayResult(
            status=status, outcome_code=outcome_code, outputs=outputs or {},
            failed_step=failed_step, expected=expected, observed=observed,
            fallback_depths=fallback_depths or {}, evidence_dir=self.evidence_dir,
        )
