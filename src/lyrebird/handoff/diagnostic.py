"""Prod deviation diagnostic (R4) — the LLM as a *diagnostic aid to a human*, never a decider.

When replay hits a genuine deviation (a verified postcondition failed and no known condition
explains it), we build a deviation handler that:
  1. asks the LLM for a SHORT human-readable explanation ("expected X, observed Y, likely
     cause…") — TEXT ONLY,
  2. writes it + the deviation to evidence and to the SessionController intervention request,
  3. hands off to a human on the same live session, and
  4. logs the deviation for OFFLINE artifact update (never live self-modification).

The invariant this preserves (brief 3.3): the LLM output here is a *message to a human*. It is
never passed to `surface.act()` — the function returns a `ReplayResult(ESCALATED)`, and the
diagnostic string is consumed only by the evidence writer / operator. Replay's *decision loop*
stays deterministic-or-human; the model only helps a person read the situation.
"""

from __future__ import annotations

from collections.abc import Callable

from lyrebird.capability.schema import Capability, ReplayResult, Step
from lyrebird.discovery.llm import LLMClient

_SYSTEM = ("You are a diagnostic assistant for a human operator supervising an automated UI "
           "replay. A recorded step did not produce its expected result. In 2-4 sentences, explain "
           "what was expected, what appears to have happened instead, and the most likely cause, so "
           "the operator can decide what to do. Do NOT issue commands or actions — only explain.")


def make_deviation_handler(
    cap: Capability,
    llm: LLMClient,
    *,
    controller,                     # SessionController — the real handoff channel
    log_deviation: Callable[[dict], None] | None = None,
) -> Callable:
    """Build the executor's `deviation_handler`. Returns a callable with the executor's
    expected signature: (step, expected, observed, obs, text, fallback_depths) -> ReplayResult."""

    def handler(step: Step, expected: str, observed: str, obs, text: str, fallback_depths) -> ReplayResult:
        # 1. LLM diagnostic — TEXT ONLY. This is the only prod LLM use; it decides nothing.
        diagnostic = _diagnose(llm, cap, step, expected, observed)

        # 2. hand off to a human with full context (same live session), via the controller.
        #    The diagnostic rides in the intervention reason so the operator sees it. wait=False:
        #    we return control to the caller (the demo/test), which drives the operator CLI.
        try:
            controller.escalate(
                step=step.index,
                reason=f"deviation: expected {expected!r} not reached. {diagnostic}",
                url=getattr(obs, "url", ""),
                screenshot="",
                state_digest=getattr(obs, "aria_digest", ""),
                capability=cap.capability_id,
                wait=False,
            )
        except Exception:
            pass  # handoff wiring is best-effort; the result still reports the deviation

        # 3. log the deviation for OFFLINE artifact update (never live mutation).
        if log_deviation is not None:
            log_deviation({"capability": cap.capability_id, "version": cap.version,
                           "step": step.index, "expected": expected, "observed": observed,
                           "diagnostic": diagnostic})

        # 4. return ESCALATED — the human is now the decision-maker. The diagnostic rides along
        #    as `observed` text; it is never fed to an action.
        return ReplayResult(status="ESCALATED", outcome_code=None, failed_step=step.index,
                            expected=expected, observed=f"DEVIATION — {diagnostic}",
                            fallback_depths=fallback_depths, evidence_dir="")

    return handler


def _diagnose(llm: LLMClient, cap: Capability, step: Step, expected: str, observed: str) -> str:
    """One LLM call that returns explanatory TEXT (no tool, no action)."""
    prompt = (f"Capability: {cap.name}\nGoal: {cap.goal or '(n/a)'}\n"
              f"Step {step.index}: action={step.action}, target={step.target.semantic_id if step.target else None}\n"
              f"Expected on the page: {expected!r}\nActually observed (excerpt): {observed!r}\n"
              "Explain for the operator.")
    try:
        # decide() forces a tool call in our client; for a pure-text diagnostic we pass a single
        # 'explain' tool and read its text arg — keeping one client API, still text-only output.
        call = llm.decide(system=_SYSTEM, messages=[{"role": "user", "content": prompt}],
                          tools=[{"name": "explain", "description": "Return the explanation text.",
                                  "input_schema": {"type": "object",
                                                   "properties": {"text": {"type": "string"}},
                                                   "required": ["text"]}}])
        return str(call.input.get("text", "")).strip() or "(no diagnostic produced)"
    except Exception as exc:  # a diagnostic failure must never crash the handoff
        return f"(diagnostic unavailable: {type(exc).__name__})"
