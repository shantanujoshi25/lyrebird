"""Interactive NL entry point for discovery (R3 redesign) — no per-site scripts.

`lyrebird discover` (no args) asks: goal → URL → credentials, infers the typed contract from
the goal, confirms it, runs the discovery loop, and records a reusable Capability. Flags
(`--goal/--url/--policy/--headless`) allow non-interactive/testable runs.

Security: the target URL is checked against the FILE-BASED allowlist (fail-closed) before any
browser launches. Credentials are collected without echo and handled as sensitive params.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import os
from pathlib import Path
from urllib.parse import urlparse

from lyrebird.discovery.contract import Contract, describe, infer_contract
from lyrebird.discovery.llm import AnthropicClient, model_from_env
from lyrebird.discovery.loop import DiscoveryLoop
from lyrebird.evidence import EvidenceWriter
from lyrebird.policy import Policy, check_action

ROOT = Path(__file__).resolve().parents[3]


def _url_allowed(policy: Policy, url: str) -> bool:
    # a navigate to the entry URL must pass the allowlist (fail-closed) before we launch.
    return check_action(policy, url=url, action_type="navigate").allowed


def _looks_like_login(goal: str) -> bool:
    g = goal.lower()
    return any(w in g for w in ("log in", "login", "sign in", "signin", "log into"))


def run_interactive(
    *,
    goal: str | None = None,
    url: str | None = None,
    policy_path: str | None = None,
    headless: bool = False,
    username: str | None = None,
    have_credentials: bool | None = None,
    prompt=input,
) -> tuple[str, str]:
    """Drive an interactive discovery. Returns (status, evidence_dir). `prompt` is injectable
    for tests. Credentials: if the user has them, collected here; else the agent will fall back
    to a human-teach login handoff during the run (R4)."""
    goal = goal or prompt("Goal (natural language): ").strip()
    url = url or prompt("Target URL: ").strip()

    policy = Policy.load(Path(policy_path) if policy_path else ROOT / "policy.yaml")
    if not _url_allowed(policy, url):
        raise SystemExit(f"Refused: {url!r} is not in the allowlist ({policy_path or 'policy.yaml'}). "
                         "Add its domain/route to the policy file first (fail-closed).")

    needs_login = _looks_like_login(goal)
    if needs_login and have_credentials is None:
        have_credentials = prompt("Do you already have a username and password? [y/N] ").strip().lower().startswith("y")
    if needs_login and have_credentials:
        username = username or prompt("Username: ").strip()
        pw = getpass.getpass("Password (not echoed, not stored in the artifact): ")
        os.environ["LYREBIRD_PARAM_USERNAME"] = username
        os.environ["LYREBIRD_PARAM_PASSWORD"] = pw
    # else: no creds provided → the discovery loop falls back to human-teach login handoff (R4).

    llm = AnthropicClient(model_from_env())
    contract = infer_contract(llm, goal, needs_login=needs_login)
    # username example (non-secret) can be echoed; password never.
    if needs_login and username:
        for p in contract.inputs:
            if p.name == "username":
                p.example = username

    print(f"\nInferred contract: {describe(contract)}")
    ans = prompt("Record with this contract? [Y/n] ").strip().lower()
    if ans.startswith("n"):
        raise SystemExit("Aborted before discovery — adjust the goal and re-run.")

    return _run_discovery(goal, url, policy, contract, headless=headless,
                          policy_path=policy_path)


def _run_discovery(goal, url, policy, contract: Contract, *, headless, policy_path):
    from lyrebird.surface.playwright_web import PlaywrightWebSurface

    run_id = f"discover-{urlparse(url).hostname or 'app'}-{_dt.datetime.now():%Y%m%d-%H%M%S}".replace(".", "_")
    sensitive = [os.environ.get(f"LYREBIRD_PARAM_{p.name.upper()}", "") for p in contract.inputs if p.sensitive]
    ev = EvidenceWriter(run_id, policy, sensitive_values=[s for s in sensitive if s])
    surface = PlaywrightWebSurface(url, headed=not headless)
    try:
        loop = DiscoveryLoop(AnthropicClient(model_from_env()), surface, policy, ev,
                             inputs=contract.inputs, outputs=contract.outputs, max_steps=30)
        result = loop.run(goal)
        ev.write_result({"status": result.status, "reason": result.reason, "outputs": result.outputs,
                         "goal": goal, "contract": describe(contract)})
        print(f"\n[discover] status={result.status}  outputs={result.outputs}")
        print(f"[discover] evidence={result.evidence_dir}")
        print(f"[discover] to build the artifact: lyrebird record {run_id}")
        return result.status, result.evidence_dir
    finally:
        surface.close()
