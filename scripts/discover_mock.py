"""Scripted mock discovery — a genuine LLM run, driven non-interactively.

Reads the goal/URL/credentials from env so it can run without stdin (the interactive
`lyrebird discover` prompts for a password via getpass, which needs a real TTY). Credentials
come from MOCK_USERNAME/MOCK_PASSWORD in .env, never hard-coded here. Same discovery loop a
human `lyrebird discover` would drive: infer the typed contract, then observe→act→read_value.
"""

from __future__ import annotations

import os
from pathlib import Path

from lyrebird.discovery.contract import describe, infer_contract
from lyrebird.discovery.interactive import _run_discovery
from lyrebird.discovery.llm import AnthropicClient, model_from_env
from lyrebird.policy import Policy

GOAL = "Sign in, look up member 100001, and read their savings balance."
URL = "http://127.0.0.1:8000/login"


def main() -> None:
    username = os.environ["MOCK_USERNAME"]
    os.environ["LYREBIRD_PARAM_USERNAME"] = username
    os.environ["LYREBIRD_PARAM_PASSWORD"] = os.environ["MOCK_PASSWORD"]

    policy = Policy.load(Path("policy.yaml"))
    llm = AnthropicClient(model_from_env())
    contract = infer_contract(llm, GOAL, needs_login=True)
    for p in contract.inputs:  # non-secret username example can be echoed
        if p.name == "username":
            p.example = username
    print(f"Inferred contract: {describe(contract)}")

    status, evidence_dir = _run_discovery(GOAL, URL, policy, contract, headless=True, policy_path=None)
    print(f"\nSTATUS={status}\nEVIDENCE={evidence_dir}")


if __name__ == "__main__":
    main()
