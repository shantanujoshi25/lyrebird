"""The surface-neutral perceive/act contract.

These models deliberately contain NO browser vocabulary. An `Element` is described the way
both a browser accessibility tree and an OS accessibility API can describe it: a role, an
accessible name, a value, a bounding box, and a *flattened* container path (frames on the
web; window/pane hierarchy on desktop). This is what lets the artifact and the replay
engine be surface-agnostic — see docs/01_ARCHITECTURE.md §3 and the A12 invariant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

ActionKind = Literal["click", "type", "select", "scroll", "navigate", "press"]


class Viewport(BaseModel):
    """Physical dimensions of the surface. Pinned so bbox locators are interpretable (A14)."""

    width: int
    height: int
    device_scale_factor: float = 1.0


class Element(BaseModel):
    """One interactable, described in surface-neutral terms.

    `index` is the handle the LLM (discovery) picks; it is stable only within a single
    Observation. Replay does not use `index` — it re-finds elements by semantic locator.
    """

    index: int
    role: str                     # ARIA/AX role: button, textbox, link, combobox, radio, checkbox, cell...
    name: str                     # accessible name (label text / aria-label / value)
    value: str | None = None      # current value for inputs
    bbox: tuple[int, int, int, int]  # (x, y, w, h) in viewport px — last-resort locator only
    container_path: list[str] = []   # frame/window hierarchy, FLATTENED (e.g. ["frame:workspace"])
    nearby_text: list[str] = []      # for label-proximity / relative-anchor strategies


class Observation(BaseModel):
    """A single perception of the surface."""

    url: str                      # semantic locator input + allowlist check
    title: str
    elements: list[Element]       # frames flattened into one list
    screenshot_png: bytes         # for LLM vision (discovery) and evidence (masked before write)
    aria_digest: str              # hash of the a11y structure — the per-page fingerprint (drift signal)


class ActionResult(BaseModel):
    ok: bool
    error: str | None = None
    settled: bool = True          # did the surface reach a stable state after the action


class Action(BaseModel):
    """An action to perform.

    Discovery supplies `target_index` (an index into the current Observation's elements).
    Replay supplies a resolved element handle out-of-band (the resolver picks it), so at the
    Surface layer replay also arrives as a `target_index` after resolution — keeping act()
    uniform. `value` may be a literal or a "{{param}}" reference (resolved before act()).
    """

    kind: ActionKind
    target_index: int | None = None
    value: str | None = None


@runtime_checkable
class Surface(Protocol):
    """Perceive and act on a surface. The single seam that extends to legacy web and desktop.

    `@runtime_checkable` lets tests assert `isinstance(impl, Surface)` — the cheap proof that
    a stub (DesktopSurface/LegacyWebSurface) satisfies the protocol without a browser (A12.3).
    """

    def observe(self) -> Observation: ...
    def act(self, action: Action) -> ActionResult: ...
    def snapshot(self, dir: Path) -> None: ...
    @property
    def viewport(self) -> Viewport: ...
