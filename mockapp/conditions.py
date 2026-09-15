"""Injectable runtime conditions — one per replay error-taxonomy branch.

Each condition is triggered by the `?inject=<name>` query param (or, for session expiry,
a per-session request counter). The mapping to the replay taxonomy (docs/01_ARCHITECTURE.md
§4/§8) is the contract the C5 replay tests assert against:

    business_outcome : NOT_FOUND, VALIDATION_ERROR, PERMISSION_DENIED
    recoverable      : INTERSTITIAL, SESSION_EXPIRED, TRANSIENT (slow load), APP_ERROR (500)
    hard             : (surfaced by replay, not injected by the app)

Keeping the registry in one place means the mock app, the HTTP tests, and the eventual
replay condition-detectors all reference the same names — no drift between them.
"""

from __future__ import annotations

from enum import Enum


class Inject(str, Enum):
    """Recognized `?inject=` values. `str` base so they compare equal to raw query strings."""

    NONE = "none"
    NOT_FOUND = "not_found"
    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    INTERSTITIAL = "interstitial"
    SESSION_EXPIRY = "session_expiry_after_n"
    SLOW_LOAD = "slow_load"
    HTTP_500 = "http_500"
    UNKNOWN_DIALOG = "unknown_dialog"   # an unexpected confirmation the flow doesn't know -> hard failure

    @classmethod
    def parse(cls, raw: str | None) -> "Inject":
        """Lenient parse: unknown or missing -> NONE (fail open to normal behavior).

        We fail *open* here on purpose: an unrecognized inject name should just render the
        normal page, not crash the mock. The taxonomy strictness lives in replay, not here.
        """
        if not raw:
            return cls.NONE
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return cls.NONE


# Slow-load delay (seconds). Small so tests stay fast but the condition-wait path in
# replay (C5) has something real to wait on rather than a no-op.
SLOW_LOAD_DELAY_S = 0.4

# Session expiry fires after this many requests within a logged-in session.
SESSION_EXPIRY_AFTER = 3
