"""Assemble and run a real discovery session (the token-spending path).

This is the glue the `lyrebird discover` CLI calls: it defines the read-only lookup goal +
its typed input/output contract, launches the real Playwright surface against the mock app,
uses the real AnthropicClient (model from env), logs redacted evidence, and prints the
result. Kept thin — the mechanics live in DiscoveryLoop, which the fake-LLM tests cover.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from lyrebird.capability.schema import InputParam, LocatorCandidate, LocatorSpec, OutputSpec
from lyrebird.discovery.llm import AnthropicClient, model_from_env
from lyrebird.discovery.loop import DiscoveryLoop
from lyrebird.evidence import EvidenceWriter
from lyrebird.policy import Policy
from lyrebird.surface.playwright_web import PlaywrightWebSurface

POLICY_YAML = Path(__file__).resolve().parents[3] / "policy.yaml"

READONLY_GOAL = (
    "Sign in with the provided credentials, then look up member 100001 and read their "
    "savings balance from the workspace."
)


def _readonly_io() -> tuple[list[InputParam], list[OutputSpec]]:
    # Login credentials are declared inputs: username is an ordinary identifier (example
    # shown to the model); password is sensitive (value read from env, never in the prompt
    # or the artifact — A7). member_id is the per-invocation lookup key.
    inputs = [
        InputParam(name="username", type="string", example="teller"),
        InputParam(name="password", type="string", sensitive=True),
        InputParam(name="member_id", type="string", example="100001"),
    ]
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


def _sensitive_runtime_values(inputs: list[InputParam]) -> list[str]:
    """Collect the runtime values of sensitive params (read from env) so the evidence writer
    can scrub them everywhere — including from surface-captured field values in ARIA dumps."""
    values: list[str] = []
    for p in inputs:
        if p.sensitive:
            v = os.environ.get(f"LYREBIRD_PARAM_{p.name.upper()}", "")
            if v:
                values.append(v)
    return values


def run_readonly_discovery(base_url: str, *, headed: bool = True) -> str:
    """Run the real LLM discovery for the read-only lookup. Returns the evidence dir."""
    run_id = f"discover-lookup-{_dt.datetime.now():%Y%m%d-%H%M%S}"
    policy = Policy.load(POLICY_YAML)
    inputs, outputs = _readonly_io()
    ev = EvidenceWriter(run_id, policy, sensitive_values=_sensitive_runtime_values(inputs))
    surface = PlaywrightWebSurface(f"{base_url}/login", headed=headed)
    try:
        llm = AnthropicClient(model_from_env())
        loop = DiscoveryLoop(llm, surface, policy, ev, inputs=inputs, outputs=outputs)
        result = loop.run(READONLY_GOAL)
        print(f"[discover] status={result.status} reason={result.reason!r}")
        print(f"[discover] outputs={result.outputs}")
        print(f"[discover] evidence={result.evidence_dir}")
        return result.evidence_dir
    finally:
        surface.close()
