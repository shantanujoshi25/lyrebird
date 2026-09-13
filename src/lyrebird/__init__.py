"""lyrebird — computer-use automation.

An LLM discovers a UI flow once (discovery); the successful trajectory becomes a typed,
versioned Capability artifact (recorder); that artifact replays deterministically without
the LLM (replay). Safety (policy), evidence, and human handoff wrap all three.

Module map (built phase by phase — see docs/02_PHASES.md):
    surface/     perceive/act seam (Surface protocol + PlaywrightWebSurface + stubs)  [C2]
    capability/  Pydantic artifact schema, versioning, JSON Schema export, store       [C4a]
    policy/      allowlist, risk classifier, redaction                                 [C6a]
    discovery/   LLM observe->decide->act loop (the ONLY module importing the LLM)     [C3]
    recorder/    trajectory -> Capability, param binding                               [C4b]
    replay/      deterministic executor, resolver, condition detector, result contract [C5]
    handoff/     session controller state machine, intervention, operator CLI          [C7]
    evidence/    run dirs, JSONL, screenshots, ARIA, trace                             [C3/C8]
"""

__version__ = "0.1.0"
