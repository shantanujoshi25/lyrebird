"""surface/ — the perceive/act seam.

Everything above this layer (discovery, recorder, replay) is surface-agnostic and speaks
only the vocabulary in `base.py` (role / name / bbox / container_path). Only the concrete
implementations below know how a particular surface is perceived and acted upon — and only
`PlaywrightWebSurface` knows the word "DOM".

    Surface (protocol)      -- base.py
    PlaywrightWebSurface    -- playwright_web.py   (the one real implementation)
    DesktopSurface          -- stubs.py            (type-checking stub, A12.3)
    LegacyWebSurface        -- stubs.py            (type-checking stub, A12.3)
"""

from lyrebird.surface.base import (
    Action,
    ActionKind,
    ActionResult,
    Element,
    Observation,
    Surface,
    Viewport,
)

__all__ = [
    "Action",
    "ActionKind",
    "ActionResult",
    "Element",
    "Observation",
    "Surface",
    "Viewport",
]
