"""The Capability artifact schema (ARCH §4) and the replay result contract.

Every field carries a WHY comment tying it to a requirement. The error taxonomy is a
first-class type (not stringly-typed) so the business/recoverable/hard distinction (R3) is
enforced by the type system rather than by convention.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ── error taxonomy: first-class types (A8) ───────────────────────────────────
class OutcomeClass(str, Enum):
    BUSINESS_OUTCOME = "business_outcome"   # a valid answer (NOT_FOUND, VALIDATION_ERROR, PERMISSION_DENIED)
    RECOVERABLE = "recoverable"             # handle & continue (INTERSTITIAL, SESSION_EXPIRED, TRANSIENT, APP_ERROR)
    HARD = "hard"                           # stop/escalate (UNKNOWN_DIALOG, LOCATOR_NOT_FOUND, CHECKPOINT_FAILED)


class OutcomeCode(str, Enum):
    # business_outcome
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    # recoverable
    INTERSTITIAL = "INTERSTITIAL"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    TRANSIENT = "TRANSIENT"
    APP_ERROR = "APP_ERROR"                 # HTTP 500 etc: retry-then-hard gradient (A8)
    # hard
    UNKNOWN_DIALOG = "UNKNOWN_DIALOG"
    LOCATOR_NOT_FOUND = "LOCATOR_NOT_FOUND"
    CHECKPOINT_FAILED = "CHECKPOINT_FAILED"


class LocatorStrategy(str, Enum):
    """The A12 invariant lives here: every member must be answerable on web AND desktop-AX.

    CSS and XPath are deliberately ABSENT — they have no desktop-AX equivalent, so a
    candidate using them would make the artifact web-only. Because this is an enum, any
    non-member value (e.g. "css") fails Pydantic validation automatically — the surface-
    agnostic invariant is enforced structurally, not by a manual check.
    """

    ROLE_NAME = "role_name"
    VISIBLE_TEXT = "visible_text"
    LABEL_PROXIMITY = "label_proximity"
    RELATIVE_ANCHOR = "relative_anchor"
    BBOX = "bbox"  # last resort; only fires when replay viewport matches provenance (A14)


class Risk(str, Enum):
    SAFE = "safe"       # reversible read/navigation
    RISKY = "risky"     # submit/confirm/transfer/delete — irreversible


# ── locators ───────────────────────────────────────────────────────────────
class LocatorCandidate(BaseModel):
    strategy: LocatorStrategy       # WHY: candidate order = fallback depth = drift signal (§7)
    args: dict[str, str] = {}       # WHY: strategy-specific (role+name / text / anchor+relation / x,y,w,h)
    confidence: float = Field(ge=0, le=1)  # WHY: recorder's trust; lower = try later / flag drift


class LocatorSpec(BaseModel):
    semantic_id: str                # WHY: stable handle ("member_search_input") — the multi-tenant override key (A5)
    candidates: list[LocatorCandidate] = Field(min_length=1)  # WHY: ordered fallback; replay requires exactly ONE match (R3)


# ── inputs / outputs: the callable contract (R2) ─────────────────────────────
class InputParam(BaseModel):
    name: str                       # WHY: bound as {{name}} in step values (A13)
    type: Literal["string", "integer", "boolean"]  # WHY: typed contract for the calling agent
    sensitive: bool = False         # WHY: creds/SSN/acct never written; masked in screenshots (A7)
    example: str | None = None      # WHY: fed to discovery so the model types the param -> binding (A13)
    description: str | None = None

    @model_validator(mode="after")
    def _sensitive_has_no_real_example(self) -> "InputParam":
        # A7 leak guard: a sensitive param's example is persisted in the artifact, so it must
        # not be a real secret. Discovery reads sensitive values from env at run time, not
        # from the artifact — so a sensitive param carries no example at all.
        if self.sensitive and self.example is not None:
            raise ValueError(
                f"sensitive input {self.name!r} must not carry an example value "
                "(sensitive values come from env at run time, never the artifact)"
            )
        return self


class OutputSpec(BaseModel):
    name: str                       # WHY: e.g. "savings_balance" — what the caller gets back
    type: Literal["string", "integer", "number", "boolean"]
    extract: "LocatorSpec"          # WHY: outputs are extracted by the same locator machinery
    transform: Literal["text", "number", "currency"] | None = None  # WHY: typed output shape (R2)


# ── conditions: the taxonomy made detectable ─────────────────────────────────
class ConditionDetector(BaseModel):
    by: Literal["text", "role", "url_pattern"]  # WHY: detect a runtime state without the LLM (R3)
    match: str


class KnownCondition(BaseModel):
    code: OutcomeCode
    klass: OutcomeClass             # WHY: decides SUCCESS-preserving vs failing (A8)
    detector: ConditionDetector
    on_detect: Literal["return_outcome", "dismiss", "retry_backoff", "relogin", "escalate"]  # WHY: deliberate response, not blind proceed (R3)
    max_retries: int = 0            # WHY: TRANSIENT/APP_ERROR retry budget, then degrade to HARD (A8)


# ── steps ────────────────────────────────────────────────────────────────────
class Step(BaseModel):
    index: int
    action: Literal["click", "type", "select", "scroll", "navigate", "press"]
    target: LocatorSpec | None = None       # WHY: null for navigate / global press
    value: str | None = None                # WHY: literal OR "{{param}}" — sensitive params never stored as literals (A13)
    wait_for: ConditionDetector | None = None      # WHY: condition-based waiting, no sleeps (§7)
    precondition: ConditionDetector | None = None
    postcondition: ConditionDetector | None = None  # WHY: verify the click worked before continuing (R3 checkpoint-per-step)
    on_conditions: list[OutcomeCode] = []          # WHY: which known conditions may fire at this step
    risk: Risk = Risk.SAFE                          # WHY: gates unattended replay (R4)
    provenance: Literal["model", "human"] = "model"  # WHY: honest attribution incl. handoff steps (A3b)
    page_fingerprint: str | None = None             # WHY: aria_digest at record time; mismatch = drift warning (§7)


# ── provenance & top level ───────────────────────────────────────────────────
class Viewport(BaseModel):
    width: int
    height: int
    device_scale_factor: float = 1.0   # WHY: bbox is meaningless across machines without this (A14)


class Provenance(BaseModel):
    discovery_run_id: str           # WHY: link to evidence dir — NOT the transcript (R2 decoupling)
    model: str                      # WHY: which model discovered it (from env)
    created_at: str                 # WHY: ISO timestamp (audit)
    viewport: Viewport              # WHY: pins bbox interpretation (A14)
    # NOTE: the raw model transcript is deliberately absent — it lives only in evidence/.


class Target(BaseModel):
    app_id: str                     # WHY: multi-tenant reuse key (R7 design)
    vendor: str                     # WHY: same vendor product across tenants
    entry_url_pattern: str          # WHY: canonicalized ("/member/:id") for allowlist + reuse
    tenant: str = "base"            # WHY: single tenant now; override layer later (A5/A11)


class Capability(BaseModel):
    schema_version: str = "1.0"     # WHY: format version — separate from capability version (A6)
    capability_id: str              # WHY: stable id across versions
    name: str
    description: str
    version: int = 1                # WHY: this capability's version (A6)
    status: Literal["draft", "approved"] = "draft"  # WHY: gates risky unattended replay (R4)
    target: Target
    inputs: list[InputParam]
    outputs: list[OutputSpec]
    steps: list[Step]
    known_conditions: list[KnownCondition]
    success_checkpoint: ConditionDetector  # WHY: the single "did we reach the goal" assertion (R2)
    risk_summary: str               # WHY: human reviewability (R2) — one-line risk note
    provenance: Provenance


# ── replay result contract (the other half of R3) ────────────────────────────
class ReplayResult(BaseModel):
    status: Literal["SUCCESS", "BUSINESS_OUTCOME", "FAILURE", "ESCALATED"]  # A8
    outcome_code: OutcomeCode | None = None   # WHY: precise machine-readable reason
    outputs: dict[str, object] = {}           # WHY: declared outputs on SUCCESS (e.g. {"savings_balance": 4210.75})
    failed_step: int | None = None            # WHY: debuggability — which step (R3)
    expected: str | None = None               # WHY: what the checkpoint/postcondition wanted
    observed: str | None = None               # WHY: what was actually seen
    fallback_depths: dict[int, int] = {}      # WHY: per-step winning-candidate index = drift telemetry (§7, R7)
    evidence_dir: str                         # WHY: pointer to logs/screenshots/trace


# resolve the forward ref used in OutputSpec.extract
OutputSpec.model_rebuild()
