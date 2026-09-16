"""C8 — guard the committed artifacts + evidence. Fast (no browser, no LLM).

Ensures the deliverables stay valid: committed artifacts still parse against the current
schema (so schema drift can't silently invalidate them), and each committed evidence run has
the expected shape and outcome. Also a redaction guard over all committed evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lyrebird.capability.schema import Capability, ReplayResult

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
EVIDENCE = ROOT / "evidence"


# ── committed artifacts still validate against the current schema ────────────
@pytest.mark.parametrize("path", sorted(ARTIFACTS.rglob("v*.json")), ids=lambda p: p.parent.name)
def test_committed_artifact_validates(path: Path) -> None:
    Capability.model_validate_json(path.read_text())  # raises if drifted


def test_capability_json_schema_exports() -> None:
    # the exported schema deliverable is present and is JSON
    schema = json.loads((ARTIFACTS / "capability.schema.json").read_text())
    assert schema["type"] == "object"


# ── the four committed runs exist with the expected outcomes ─────────────────
def test_discovery_run_committed() -> None:
    runs = list(EVIDENCE.glob("discover-lookup-*"))
    assert runs, "no committed discovery run"
    result = json.loads((runs[0] / "result.json").read_text())
    assert result["status"] == "SUCCESS"
    assert result["outputs"]["savings_balance"] == 4210.75


@pytest.mark.parametrize(
    "run, status, code",
    [
        ("replay-happy", "SUCCESS", None),
        ("replay-not-found", "BUSINESS_OUTCOME", "NOT_FOUND"),
        ("replay-escalated", "ESCALATED", None),
    ],
)
def test_committed_replay_result(run: str, status: str, code: str | None) -> None:
    result_path = EVIDENCE / run / "result.json"
    assert result_path.exists(), f"missing committed run {run}"
    result = ReplayResult.model_validate_json(result_path.read_text())  # revalidates the contract
    assert result.status == status
    if code is not None:
        assert result.outcome_code is not None and result.outcome_code.value == code


def test_escalated_run_has_real_handoff_artifacts() -> None:
    d = EVIDENCE / "replay-escalated"
    run_state = json.loads((d / "run_state.json").read_text())
    assert run_state["control"] == "PENDING_HUMAN"          # a human is needed
    req = json.loads((d / "intervention_request.json").read_text())
    assert "risky" in req["reason"].lower()                 # why we stopped


def test_happy_replay_extracted_balance() -> None:
    result = json.loads((EVIDENCE / "replay-happy" / "result.json").read_text())
    assert result["outputs"]["savings_balance"] == 4210.75


# ── redaction guard over ALL committed evidence ──────────────────────────────
def test_no_sensitive_value_in_committed_evidence() -> None:
    for path in EVIDENCE.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".txt"}:
            text = path.read_text()
            assert "demo-pass-not-secret" not in text, f"password leaked in {path}"
