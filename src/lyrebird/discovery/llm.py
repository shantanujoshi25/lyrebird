"""LLMClient — the one seam to the model provider.

The loop depends on this Protocol, not on the Anthropic SDK, so (a) the provider is
swappable and (b) tests run against `FakeLLMClient` with no network and no tokens. The
model string is read from env (`LYREBIRD_MODEL`), never hard-coded (verify-don't-assume;
catalog-verified default `claude-sonnet-4-6`).

A `decide()` call takes the running message history (text + screenshot) and the available
tool schemas, and returns the single `ToolCall` the model chose. The loop executes it,
appends the result, and calls `decide()` again — a standard tool-use loop.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    """One tool invocation chosen by the model."""

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    def decide(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ToolCall: ...


DEFAULT_MODEL = "claude-sonnet-4-6"


def model_from_env() -> str:
    return os.environ.get("LYREBIRD_MODEL", DEFAULT_MODEL)


class AnthropicClient:
    """Real client. Imported lazily so the base install (and replay) never needs the SDK."""

    def __init__(self, model: str | None = None, *, max_tokens: int = 2048) -> None:
        import anthropic  # local import: keeps the dependency out of the replay path

        self._anthropic = anthropic
        self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self.model = model or model_from_env()
        self.max_tokens = max_tokens

    def decide(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ToolCall:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            tools=tools,
            tool_choice={"type": "any"},   # force a tool call every turn — the loop is tool-driven
            messages=messages,
        )
        for block in resp.content:
            if block.type == "tool_use":
                return ToolCall(id=block.id, name=block.name, input=dict(block.input))
        # No tool call (shouldn't happen with tool_choice=any); surface as a finish with reason.
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        return ToolCall(id="none", name="finish", input={"success": False, "reason": f"no tool call: {text[:200]}"})

    @staticmethod
    def image_block(png: bytes) -> dict[str, Any]:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(png).decode()},
        }


@dataclass
class FakeLLMClient:
    """Replays a fixed script of ToolCalls. No network, no tokens — for the CI loop tests.

    Each `decide()` returns the next scripted call. This lets us prove loop mechanics,
    policy blocking, and redaction deterministically without spending anything.
    """

    script: list[ToolCall]
    _i: int = 0
    calls_seen: list[dict[str, Any]] = field(default_factory=list)

    def decide(self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ToolCall:
        self.calls_seen.append({"messages": len(messages), "tools": [t["name"] for t in tools]})
        if self._i >= len(self.script):
            return ToolCall(id="auto-finish", name="finish", input={"success": False, "reason": "script exhausted"})
        call = self.script[self._i]
        self._i += 1
        return call
