"""recorder/ — turn a discovery trajectory into a Capability artifact.

Consumes a *saved* trajectory (steps.jsonl + per-step ARIA snapshots) — not a live browser
— and produces the typed, versioned Capability that replay executes. This is the seam where
the expensive, non-deterministic discovery output becomes a cheap, deterministic contract:
each recorded element gets an ordered list of robust locator candidates, param references
become {{param}} bindings, and the raw transcript is deliberately left behind (R2). See
docs/01_ARCHITECTURE.md §4 and A13 (binding) / A14 (viewport).
"""

from lyrebird.recorder.record import record_capability

__all__ = ["record_capability"]
