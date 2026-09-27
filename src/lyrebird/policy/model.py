"""The typed Policy — loaded from policy.yaml, fail-closed.

Fail-closed means: if a field is missing the default is the *restrictive* one (empty
allowlists deny everything), and loading a malformed/absent file raises rather than
silently permitting. The safety posture must never degrade to "allow" by accident.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Allowlist(BaseModel):
    domains: list[str] = Field(default_factory=list)       # empty => deny all (fail-closed)
    url_patterns: list[str] = Field(default_factory=list)  # glob patterns; empty => deny all
    action_types: list[str] = Field(default_factory=list)  # empty => deny all


class RiskRules(BaseModel):
    risky_text_patterns: list[str] = Field(default_factory=list)
    risky_roles: list[str] = Field(default_factory=list)


class RedactionRules(BaseModel):
    always_sensitive_params: list[str] = Field(default_factory=list)
    identifier_params: list[str] = Field(default_factory=list)
    scrub_patterns: list[str] = Field(default_factory=list)
    screenshot_mask: str = "sensitive_inputs"


class Policy(BaseModel):
    allowlist: Allowlist = Field(default_factory=Allowlist)
    risk: RiskRules = Field(default_factory=RiskRules)
    redaction: RedactionRules = Field(default_factory=RedactionRules)

    @classmethod
    def load(cls, path: Path | str) -> Policy:
        path = Path(path)
        if not path.exists():
            # fail-closed: a missing policy is an error, not an implicit allow-all
            raise FileNotFoundError(f"policy file not found: {path}")
        data = yaml.safe_load(path.read_text()) or {}
        return cls.model_validate(data)
