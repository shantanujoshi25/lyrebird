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

    d = sub.add_parser("discover", help="Run the LLM discovery loop (C3). Spends tokens.")
    d.add_argument("--url", default="http://127.0.0.1:8000", help="base URL of the running mock app")
    d.add_argument("--headless", action="store_true", help="run the browser headless")

    sub.add_parser("replay", help="Deterministically replay a capability (C5).")
    sub.add_parser("operator", help="Operator handoff console (C7).")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "discover":
        from lyrebird.discovery.run import run_readonly_discovery

        run_readonly_discovery(args.url, headed=not args.headless)
        return 0
    print(f"'{args.command}' is not implemented yet — it lands in its construction phase.")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
