"""lyrebird CLI entry point.

Verbs are wired up in their respective phases:
    discover  -> discovery loop        [C3]
    replay    -> deterministic replay  [C5]
    operator  -> handoff operator CLI  [C7]

For now this is a stub so `lyrebird` is installable and importable (C0/C1). Each phase
replaces its placeholder branch with the real subcommand.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lyrebird", description="Computer-use automation.")
    sub = parser.add_subparsers(dest="command")

    d = sub.add_parser("discover", help="Interactive NL discovery: goal + URL. Spends tokens.")
    d.add_argument("--goal", default=None, help="natural-language goal (prompts if omitted)")
    d.add_argument("--url", default=None, help="target URL (prompts if omitted)")
    d.add_argument("--policy", default=None, help="allowlist policy file (default: policy.yaml)")
    d.add_argument("--headless", action="store_true", help="run the browser headless")
    d.add_argument("--username", default=None, help="login username (non-secret; prompts if a login flow)")
    d.add_argument("--have-credentials", dest="have_credentials", action="store_true",
                   help="you will provide credentials (password prompted, never echoed)")

    r = sub.add_parser("replay", help="Deterministically replay a capability (C5). No LLM.")
    r.add_argument("capability", help="capability id (e.g. lookup_savings_balance)")
    r.add_argument("--url", default="http://127.0.0.1:8000", help="base URL of the running mock app")
    r.add_argument("--param", action="append", default=[], metavar="k=v", help="a NON-secret input param (repeatable)")
    r.add_argument("--secrets", default="secrets.yaml", help="YAML file with sensitive param values (default: secrets.yaml)")
    r.add_argument("--approve", action="store_true", help="treat the artifact as approved (for risky steps)")
    r.add_argument("--confirm-risky", action="store_true", help="opt in to running risky steps")
    r.add_argument("--pre-login", action="store_true", help="log in before the capability (for capabilities that start mid-flow)")
    r.add_argument("--pre-nav", default=None, help="navigate here after login (e.g. /member/100001/subaccount)")
    r.add_argument("--headed", action="store_true", help="show the browser")

    # operator delegates its own arg parsing to the handoff CLI (list/take/handback).
    sub.add_parser("operator", help="Operator handoff console (C7).", add_help=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, rest = parser.parse_known_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "discover":
        from lyrebird.discovery.interactive import run_interactive

        status, _ = run_interactive(
            goal=args.goal, url=args.url, policy_path=args.policy, headless=args.headless,
            username=args.username,
            have_credentials=True if args.have_credentials else None,
        )
        return 0 if status == "SUCCESS" else 1
    if args.command == "replay":
        from lyrebird.replay.run import replay_run

        params = dict(kv.split("=", 1) for kv in args.param)
        status, _ = replay_run(
            args.capability, args.url, params=params, secrets_path=args.secrets,
            status_override="approved" if args.approve else None,
            confirm_risky=args.confirm_risky, pre_login=args.pre_login,
            pre_nav=args.pre_nav, headed=args.headed,
        )
        return 0 if status in ("SUCCESS", "BUSINESS_OUTCOME", "ESCALATED") else 1
    if args.command == "operator":
        from lyrebird.handoff.operator_cli import main as operator_main

        return operator_main(rest)  # list / take / handback
    print(f"'{args.command}' is not implemented yet — it lands in its construction phase.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
