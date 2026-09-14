"""System prompt and goal framing for discovery.

The prompt gives the model the declared input schema WITH example values (A13) so it can
type parameters (not literals), and tells it to work by element index from the observation.
It is deliberately terse and non-prescriptive — per the model guidance, over-scripted
prompts reduce quality; state the goal and the contract, not a step-by-step.
"""

from __future__ import annotations

from lyrebird.capability.schema import InputParam, OutputSpec

SYSTEM = """You are driving a legacy back-office web application to accomplish a goal, the \
way a human operator would. You cannot see a clean DOM — you work from a screenshot and a \
numbered list of interactable elements, and you act by element index.

Rules:
- After any action that changes the page, call `observe` to get a fresh element list before \
acting again. Indices are only valid for the most recent observation.
- To enter a value that corresponds to a declared input parameter, use `type` with `param` \
set to the parameter name (NOT a literal value). This keeps the recorded flow reusable and \
avoids persisting real values.
- Stay within the application. Do not attempt actions outside the goal.
- When the goal is achieved, call `finish` with success=true and the observed value for \
each declared output. If you get stuck or reach a dead end, call `finish` with success=false \
and a brief reason.
Be efficient: prefer the shortest correct path."""


def goal_prompt(goal: str, inputs: list[InputParam], outputs: list[OutputSpec]) -> str:
    """The initial user turn: the natural-language goal + the typed input/output contract."""
    lines = [f"GOAL: {goal}", "", "DECLARED INPUTS (type these by param name):"]
    for p in inputs:
        ex = "" if p.sensitive else f" (example: {p.example})"
        sens = " [SENSITIVE — read from environment, never shown here]" if p.sensitive else ""
        lines.append(f"  - {p.name}: {p.type}{ex}{sens}")
    lines += ["", "DECLARED OUTPUTS (read and return these on success):"]
    for o in outputs:
        lines.append(f"  - {o.name}: {o.type}")
    lines += ["", "Begin by calling `observe`."]
    return "\n".join(lines)
