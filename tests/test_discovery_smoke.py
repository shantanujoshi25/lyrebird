"""C3 — discovery loop mechanics with a FAKE LLM. No network, no tokens.

Proves the loop drives the read-only flow to success, blocks off-allowlist actions and
escalates, and never writes a sensitive value to steps.jsonl. The real LLM run is separate
(manual, token-spending) and its evidence is committed under evidence/.

The fake "LLM" is scripted by INTENT (role + name), resolving the element index against the
loop's own observation messages at decide() time — so the script doesn't hardcode brittle
integer indices but still exercises the real index-based act() path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from lyrebird.capability.schema import InputParam, OutputSpec, LocatorCandidate, LocatorSpec
from lyrebird.discovery.llm import ToolCall
from lyrebird.discovery.loop import DiscoveryLoop
from lyrebird.evidence import EvidenceWriter
from lyrebird.policy import Policy
from lyrebird.surface.playwright_web import PlaywrightWebSurface

pytestmark = pytest.mark.slow  # browser-driven

POLICY_YAML = Path(__file__).resolve().parents[1] / "policy.yaml"
MEMBER = "100001"


@dataclass
class Intent:
    """A scripted step, resolved to an index against the latest observation text."""

    tool: str
    role: str = ""
    name: str = ""
    nearby: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScriptedLLM:
    """Resolves each Intent's element index from the most recent observation message."""

    intents: list[Intent]
    _i: int = 0

    def decide(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ToolCall:
        if self._i >= len(self.intents):
            return ToolCall(id="fin", name="finish", input={"success": False, "reason": "script done"})
        intent = self.intents[self._i]
        self._i += 1
        if intent.tool in ("observe", "finish", "navigate"):
            return ToolCall(id=f"c{self._i}", name=intent.tool, input=dict(intent.extra))
        idx = self._find_index(messages, intent)
        return ToolCall(id=f"c{self._i}", name=intent.tool, input={"index": idx, **intent.extra})

    def _find_index(self, messages: list[dict[str, Any]], intent: Intent) -> int:
        text = self._latest_observation(messages)
        # element lines look like:  [3] textbox 'Member ID' near=['Member ID ...']
        for line in text.splitlines():
            m = re.match(r"\s*\[(\d+)\]\s+(\S+)\s+'([^']*)'(.*)", line)
            if not m:
                continue
            idx, role, name, tail = int(m.group(1)), m.group(2), m.group(3), m.group(4)
            if intent.role and role != intent.role:
                continue
            if intent.name and intent.name.lower() not in name.lower():
                continue
            if intent.nearby and intent.nearby.lower() not in tail.lower():
                continue
            return idx
        raise AssertionError(f"no element for {intent} in:\n{text}")

    @staticmethod
    def _latest_observation(messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            content = msg.get("content", "")
            # observation messages are a list of blocks: [{text: "URL:..."}, {image...}]
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "text" and block["text"].startswith("URL:"):
                        return block["text"]
            elif isinstance(content, str) and content.startswith("URL:"):
                return content
        return ""


@pytest.fixture
def policy() -> Policy:
    return Policy.load(POLICY_YAML)


@pytest.fixture
def surface(live_server: str) -> PlaywrightWebSurface:
    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    yield s
    s.close()


def _io() -> tuple[list[InputParam], list[OutputSpec]]:
    inputs = [InputParam(name="member_id", type="string", example=MEMBER)]
    outputs = [
        OutputSpec(
            name="savings_balance",
            type="number",
            extract=LocatorSpec(
                semantic_id="savings_balance_cell",
                candidates=[LocatorCandidate(strategy="label_proximity", args={"label": "Savings"}, confidence=0.9)],
            ),
            transform="currency",
        )
    ]
    return inputs, outputs


def test_loop_drives_readonly_flow_to_success(surface, policy, tmp_path) -> None:
    inputs, outputs = _io()
    ev = EvidenceWriter("test-discovery", policy, root=tmp_path)
    # Observation is automatic after each action (and once at the start), so the script is
    # pure actions — indices resolve against the latest auto-observation.
    llm = ScriptedLLM(
        intents=[
            Intent("type", role="textbox", nearby="Username", extra={"value": "teller"}),
            Intent("type", role="textbox", nearby="Password", extra={"value": "demo-pass-not-secret"}),
            Intent("click", role="button", name="Sign in"),
            Intent("type", role="textbox", nearby="Member ID", extra={"param": "member_id"}),
            Intent("click", role="button", name="Search"),
            Intent("finish", extra={"success": True, "reason": "reached member detail", "outputs": {"savings_balance": 4210.75}}),
        ]
    )
    loop = DiscoveryLoop(llm, surface, policy, ev, inputs=inputs, outputs=outputs)
    result = loop.run("Look up member 100001 and read their savings balance")

    assert result.status == "SUCCESS", result.reason
    # the member_id was typed as a BINDING, never the literal, in the trajectory
    type_steps = [s for s in result.trajectory if s.tool == "type" and s.tool_input.get("param") == "member_id"]
    assert type_steps and type_steps[0].bound_value == "{{member_id}}"


def test_policy_block_escalates(surface, policy, tmp_path) -> None:
    inputs, outputs = _io()
    ev = EvidenceWriter("test-block", policy, root=tmp_path)
    # try to navigate off-allowlist -> check_action denies -> loop escalates
    llm = ScriptedLLM(intents=[Intent("navigate", extra={"url": "http://evil.example.com/x"})])
    loop = DiscoveryLoop(llm, surface, policy, ev, inputs=inputs, outputs=outputs)
    result = loop.run("do something out of bounds")
    assert result.status == "ESCALATED"
    assert "policy blocked" in result.reason


def test_sensitive_value_never_written_to_evidence(surface, policy, tmp_path, monkeypatch) -> None:
    # a sensitive param whose value is provided via env must not appear in steps.jsonl
    monkeypatch.setenv("LYREBIRD_PARAM_PASSWORD", "s3cr3t-env-pw")
    inputs = [
        InputParam(name="member_id", type="string", example=MEMBER),
        InputParam(name="password", type="string", sensitive=True),
    ]
    _, outputs = _io()
    ev = EvidenceWriter("test-redact", policy, root=tmp_path, sensitive_values=["s3cr3t-env-pw"])
    llm = ScriptedLLM(
        intents=[
            Intent("type", role="textbox", nearby="Password", extra={"param": "password"}),
            Intent("finish", extra={"success": False, "reason": "stop"}),
        ]
    )
    loop = DiscoveryLoop(llm, surface, policy, ev, inputs=inputs, outputs=outputs)
    loop.run("type the password")

    run_dir = Path(tmp_path) / "test-redact"
    steps = (run_dir / "steps.jsonl").read_text()
    assert "s3cr3t-env-pw" not in steps       # the resolved sensitive value never hits disk
    assert "{{password}}" in steps            # only the binding is recorded

    # regression guard for the real-run leak: the surface captures the password field's
    # `value` into ARIA snapshots; NONE of the evidence files may contain the raw value.
    for path in run_dir.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".txt"}:
            assert "s3cr3t-env-pw" not in path.read_text(), f"leak in {path.name}"
