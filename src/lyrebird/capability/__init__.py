"""capability/ — the typed, versioned Capability artifact and its serialization.

This is a leaf module: it depends on nothing else in lyrebird and imports no browser and no
LLM. It is the contract that lets the LLM-driven half (discovery) hand off to the LLM-free
half (replay). Schema design is a focal point of the evaluation (R2) — every field earns
its place; see docs/01_ARCHITECTURE.md §4.
"""

from lyrebird.capability.schema import (
    Capability,
    ConditionDetector,
    InputParam,
    KnownCondition,
    LocatorCandidate,
    LocatorSpec,
    LocatorStrategy,
    OutcomeClass,
    OutcomeCode,
    OutputSpec,
    Provenance,
    ReplayResult,
    Risk,
    Step,
    Target,
    Viewport,
)
from lyrebird.capability.jsonschema_export import export_schema
from lyrebird.capability.store import CapabilityStore

__all__ = [
    "Capability",
    "CapabilityStore",
    "ConditionDetector",
    "InputParam",
    "KnownCondition",
    "LocatorCandidate",
    "LocatorSpec",
    "LocatorStrategy",
    "OutcomeClass",
    "OutcomeCode",
    "OutputSpec",
    "Provenance",
    "ReplayResult",
    "Risk",
    "Step",
    "Target",
    "Viewport",
    "export_schema",
]
