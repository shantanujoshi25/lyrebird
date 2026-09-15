"""The mock operator console (R6 says the console may be mocked; the mechanism must be real).

A separate process from the engine. It reads/writes the same run-state files the engine
polls, so `take`/`handback` genuinely transfer control of the live session. Three verbs:

    list                 show every run and who is in control
    take <run> [--as X]  take control of a PENDING_HUMAN run (-> HUMAN)
    handback <run> [--note ...]  return control to automation (-> AUTOMATION)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lyrebird.handoff.controller import SessionController
from lyrebird.handoff.intervention import InterventionRequest
from lyrebird.handoff.runstate import Control, RunState

EVIDENCE_ROOT = Path("evidence")


def _run_dirs(root: Path) -> list[Path]:
    return sorted(d for d in root.glob("*") if (d / "run_state.json").exists())


def cmd_list(root: Path) -> int:
    rows = _run_dirs(root)
    if not rows:
        print("no runs with a run-state file under", root)
        return 0
    print(f"{'RUN':40} {'CONTROL':14} {'STEP':>4}  REASON")
    for d in rows:
        s = RunState.read(d)
        marker = " <-- needs a human" if s.control is Control.PENDING_HUMAN else ""
        print(f"{d.name:40} {s.control.value:14} {s.current_step:>4}  {s.reason}{marker}")
    return 0


def cmd_take(root: Path, run: str, operator: str) -> int:
    d = root / run
    req_path = InterventionRequest.path(d)
    if req_path.exists():
        req = InterventionRequest.read(d)
        print(f"intervention: {req.reason}\n  at step {req.step}, url {req.url}\n  screenshot: {req.screenshot}")
    state = SessionController.operator_take(d, operator)
    print(f"[take] {run}: control now {state.control.value} (operator={operator})")
    print("You now control the same live session. Perform the manual steps, then run `handback`.")
    return 0


def cmd_handback(root: Path, run: str, note: str) -> int:
    state = SessionController.operator_handback(root / run, note)
    print(f"[handback] {run}: control returned to {state.control.value}. note={note!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lyrebird operator", description="Operator handoff console.")
    parser.add_argument("--evidence", default=str(EVIDENCE_ROOT), help="evidence root dir")
    sub = parser.add_subparsers(dest="op", required=True)
    sub.add_parser("list")
    t = sub.add_parser("take"); t.add_argument("run"); t.add_argument("--as", dest="operator", default="operator")
    h = sub.add_parser("handback"); h.add_argument("run"); h.add_argument("--note", default="")
    args = parser.parse_args(argv)
    root = Path(args.evidence)

    if args.op == "list":
        return cmd_list(root)
    if args.op == "take":
        return cmd_take(root, args.run, args.operator)
    if args.op == "handback":
        return cmd_handback(root, args.run, args.note)
    return 1
