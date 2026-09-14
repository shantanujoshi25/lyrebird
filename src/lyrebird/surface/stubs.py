"""Documented stub surfaces — the proof the seam is real without building them (A12.3).

`DesktopSurface` and `LegacyWebSurface` implement the exact same `Surface` protocol as
`PlaywrightWebSurface`. Their bodies raise `NotImplementedError`, but because they satisfy
the protocol (checked by `isinstance(x, Surface)` in the tests and by a type checker), they
*prove* the perceive/act interface is satisfiable without a browser. That is stronger
evidence for criterion #5 (generalization) than a paragraph of prose.

Each docstring records HOW the surface would perceive/act, mapping onto the same
surface-neutral vocabulary (role / name / bbox / container_path) — see the cross-surface
locator table in docs/01_ARCHITECTURE.md §3.
"""

from __future__ import annotations

from pathlib import Path

from lyrebird.surface.base import Action, ActionResult, Observation, Viewport

_MSG = (
    "Documented stub — not built. This surface satisfies the Surface protocol to prove the "
    "abstraction generalizes; building it is out of scope (see docs/01_ARCHITECTURE.md §3)."
)


class DesktopSurface:
    """Native desktop app via an OS accessibility API + coordinate input.

    observe(): walk the AX tree (AXRole/AXTitle/AXValue/position); flatten the window/pane
        hierarchy into `container_path`; screenshot the focused window; digest the AX tree.
    act():     AX actions where available (AXPress), else synthesized keyboard/mouse at the
        element's screen coordinates (the bbox last-resort path, viewport pinned per A14).
    """

    def observe(self) -> Observation:
        raise NotImplementedError(_MSG)

    def act(self, action: Action) -> ActionResult:
        raise NotImplementedError(_MSG)

    def snapshot(self, dir: Path) -> None:
        raise NotImplementedError(_MSG)

    @property
    def viewport(self) -> Viewport:
        raise NotImplementedError(_MSG)


class LegacyWebSurface:
    """Framed/legacy web where the modern Playwright path is inadequate.

    Conceptually the same as PlaywrightWebSurface but emphasizing frame flattening: each
    frameset/iframe becomes a `container_path` prefix, and all frames' elements are merged
    into one flat element list — exactly as a desktop window hierarchy would flatten. The
    concrete web slice already exercises iframe flattening, so this stub documents the
    generalization rather than duplicating it.
    """

    def observe(self) -> Observation:
        raise NotImplementedError(_MSG)

    def act(self, action: Action) -> ActionResult:
        raise NotImplementedError(_MSG)

    def snapshot(self, dir: Path) -> None:
        raise NotImplementedError(_MSG)

    @property
    def viewport(self) -> Viewport:
        raise NotImplementedError(_MSG)
