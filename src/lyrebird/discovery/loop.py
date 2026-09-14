"""The discovery loop: observe -> decide -> policy check -> act, with stop conditions.

Produces a raw *trajectory* (the recorder in C4b turns it into a Capability) plus redacted
evidence. The loop, not the model, owns safety and stopping:

  * every action is checked against the allowlist BEFORE it runs (R4); a denial escalates.
  * `type` with a `param` records the binding "{{param}}" (never the resolved value); the
    resolved value comes from the input's example (non-sensitive) or env (sensitive, A7).
  * stop conditions: finish(success) -> DONE; max steps or a repeated state hash (dead end)
    -> escalation (R6/A3b — full handoff is C7; here we mark the run and stop).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from lyrebird.capability.schema import InputParam, OutputSpec
from lyrebird.discovery.llm import AnthropicClient, LLMClient, ToolCall
from lyrebird.discovery.prompts import SYSTEM, goal_prompt
from lyrebird.discovery.tools import TOOLS
from lyrebird.evidence import EvidenceWriter
from lyrebird.policy import Policy, check_action
from lyrebird.surface.base import Action, Surface


@dataclass
class TrajectoryStep:
    index: int
    tool: str
    tool_input: dict[str, Any]
    bound_value: str | None      # the "{{param}}" binding or literal actually sent to the surface
    url_before: str
    ok: bool
    note: str = ""


@dataclass
class DiscoveryResult:
    status: str                  # "SUCCESS" | "ESCALATED" | "DEAD_END"
    reason: str
    trajectory: list[TrajectoryStep] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)
    evidence_dir: str = ""


class DiscoveryLoop:
    def __init__(
        self,
        llm: LLMClient,
        surface: Surface,
        policy: Policy,
        evidence: EvidenceWriter,
        *,
        inputs: list[InputParam],
        outputs: list[OutputSpec],
        max_steps: int = 25,
    ) -> None:
        self._llm = llm
        self._surface = surface
        self._policy = policy
        self._evidence = evidence
        self._inputs = {p.name: p for p in inputs}
        self._outputs = outputs
        self._max_steps = max_steps

    # ── param resolution (A13/A7) ────────────────────────────────────────
    def _resolve_param(self, name: str) -> tuple[str, str]:
        """Return (binding_recorded, value_sent). Binding is '{{name}}'; value is example/env."""
        param = self._inputs.get(name)
        if param is None:
            return (f"{{{{{name}}}}}", "")  # unknown param -> empty value, still record binding
        if param.sensitive:
            value = os.environ.get(f"LYREBIRD_PARAM_{name.upper()}", "")  # sensitive -> env only
        else:
            value = param.example or ""
        return (f"{{{{{name}}}}}", value)

    # ── run ───────────────────────────────────────────────────────────────
    def run(self, goal: str) -> DiscoveryResult:
        # Seed with the goal AND the first observation (screenshot + elements), so the model
        # starts from a real perception rather than having to spend a turn asking to observe.
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": goal_prompt(goal, list(self._inputs.values()), self._outputs)}
        ]
        traj: list[TrajectoryStep] = []
        step_i = 0
        self._append_observation(messages, step_i)
        recent_actions: list[str] = []

        while step_i < self._max_steps:
            call = self._llm.decide(system=SYSTEM, messages=messages, tools=TOOLS)

            if call.name == "finish":
                success = bool(call.input.get("success"))
                reason = str(call.input.get("reason", ""))
                if success:
                    return self._finish(DiscoveryResult("SUCCESS", reason, traj, dict(call.input.get("outputs") or {})))
                return self._finish(DiscoveryResult("DEAD_END", reason or "model declared dead end", traj))

            if call.name == "observe":
                # An explicit observe is allowed but usually unnecessary now that every action
                # is followed by an automatic fresh observation. Honor it without spending a step.
                self._append_observation(messages, step_i)
                continue

            # an acting tool: policy-check BEFORE executing (R4).
            # For navigate, check the TARGET url, not the current page — otherwise a navigate
            # to a disallowed domain would slip through.
            action_type = _tool_to_action(call.name)
            url = str(call.input["url"]) if call.name == "navigate" and call.input.get("url") else self._surface.observe().url
            decision = check_action(self._policy, url=url, action_type=action_type)
            if not decision.allowed:
                self._log(step_i, call, None, url, False, f"POLICY BLOCK: {decision.reason}")
                return self._finish(DiscoveryResult("ESCALATED", f"policy blocked: {decision.reason}", traj))

            # dead-end guard: the model repeating the exact same action is stuck (this is the
            # failure mode a blind observe->act loop falls into). Escalate rather than burn steps.
            sig = f"{call.name}:{call.input.get('index')}:{call.input.get('param') or call.input.get('value') or call.input.get('url')}:{url}"
            recent_actions.append(sig)
            if recent_actions[-3:].count(sig) >= 3:
                self._log(step_i, call, None, url, False, "repeated action (dead end)")
                return self._finish(DiscoveryResult("ESCALATED", f"repeated action, stuck: {sig}", traj))

            binding, result_note, ok = self._act(call)
            traj.append(TrajectoryStep(step_i, call.name, dict(call.input), binding, url, ok, result_note))
            self._log(step_i, call, binding, url, ok, result_note)
            step_i += 1
            # THE KEY FIX: after acting, feed back a fresh observation (screenshot + elements)
            # so the model can see the effect of what it just did and decide the next step.
            messages.append({"role": "user", "content": _tool_result_text(call, ok, result_note)})
            self._append_observation(messages, step_i)

        return self._finish(DiscoveryResult("ESCALATED", "max steps reached", traj))

    def _append_observation(self, messages: list[dict[str, Any]], step_i: int) -> None:
        """Observe, persist redacted evidence, and append the observation (text + image) to messages."""
        obs = self._surface.observe()
        self._evidence.save_screenshot(step_i, obs.screenshot_png)
        self._evidence.save_aria(step_i, [e.model_dump() for e in obs.elements])
        messages.append(self._observation_message(obs))

    # ── helpers ──────────────────────────────────────────────────────────
    def _act(self, call: ToolCall) -> tuple[str | None, str, bool]:
        kind = _tool_to_action(call.name)
        binding: str | None = None
        value: str | None = call.input.get("value")

        if call.name == "type" and call.input.get("param"):
            binding, value = self._resolve_param(str(call.input["param"]))
        elif call.name == "type":
            binding = value  # literal typed value is recorded as-is (may be flagged inferred in C4b)

        action = Action(
            kind=kind,  # type: ignore[arg-type]
            target_index=call.input.get("index"),
            value=(call.input.get("url") if call.name == "navigate"
                   else call.input.get("key") if call.name == "press"
                   else value),
        )
        res = self._surface.act(action)
        return binding, (res.error or "ok"), res.ok

    def _observation_message(self, obs) -> dict[str, Any]:
        lines = [f"URL: {obs.url}", f"Title: {obs.title}", "Elements:"]
        for e in obs.elements:
            frame = f" [{'/'.join(e.container_path)}]" if e.container_path else ""
            near = f" near={e.nearby_text}" if e.nearby_text else ""
            val = f" value={e.value!r}" if e.value else ""
            lines.append(f"  [{e.index}] {e.role} {e.name!r}{val}{frame}{near}")
        text = "\n".join(lines)
        # Content is a list: the screenshot (Set-of-Marks vision) + the element list. The
        # text always starts with "URL:" so the observation is findable in history (the fake
        # LLM keys on that); real Claude uses the image + text together.
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                AnthropicClient.image_block(obs.screenshot_png),
            ],
        }

    def _log(self, i: int, call: ToolCall, binding: str | None, url: str, ok: bool, note: str) -> None:
        self._evidence.log_step(
            {"step": i, "tool": call.name, "input": call.input, "binding": binding, "url": url, "ok": ok, "note": note}
        )

    def _finish(self, result: DiscoveryResult) -> DiscoveryResult:
        result.evidence_dir = str(self._evidence.dir)
        self._evidence.write_result(
            {"status": result.status, "reason": result.reason, "outputs": result.outputs,
             "steps": [s.__dict__ for s in result.trajectory]}
        )
        return result


_TOOL_ACTION = {"click": "click", "type": "type", "select": "select", "scroll": "scroll",
                "navigate": "navigate", "press": "press"}


def _tool_to_action(tool: str) -> str:
    return _TOOL_ACTION.get(tool, tool)


def _tool_result_text(call: ToolCall, ok: bool, note: str) -> str:
    return f"[{call.name}] {'ok' if ok else 'error'}: {note}"
