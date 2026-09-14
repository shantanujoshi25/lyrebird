"""PlaywrightWebSurface — the one real Surface implementation.

The ONLY module that knows the word "DOM". It launches a (headed by default) Chromium,
perceives the page as a surface-neutral `Observation`, and acts by element index. Frames
and iframes are flattened into a single element list so everything above this layer never
has to know a frame existed — the same flattening a desktop window hierarchy would do.

Perception strategy: a single injected JS pass per frame collects interactables with their
computed role, accessible-ish name, value, viewport-space bounding box, and nearby text.
Boxes are offset by the frame's own bounding box so all coordinates are in top-level
viewport space (which is what screenshots and bbox locators need). Each element is tagged
with a stable data attribute (`data-lb-idx`) so `act()` can re-find exactly the element
`observe()` reported, without re-running the heuristic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from playwright.sync_api import Frame, Page, sync_playwright

from lyrebird.surface.base import Action, ActionResult, Element, Observation, Viewport

# JS run inside EACH frame. Returns interactables (role, name, value, box, nearby text) and
# stamps each with data-lb-idx so act() can select it precisely. `base` is the frame's own
# offset in top-level viewport coords; boxes are shifted into that space.
_COLLECT_JS = r"""
(base) => {
  const SEL = 'a,button,input,select,textarea,[role],[onclick]';
  const roleFor = (el) => {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'submit' || t === 'button') return 'button';
      return 'textbox';
    }
    return tag;
  };
  const nameFor = (el, role) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    // radios/checkboxes: text immediately after the control is the visual label
    if (role === 'radio' || role === 'checkbox') {
      const sib = el.nextSibling;
      if (sib && sib.nodeType === 3 && sib.textContent.trim()) return sib.textContent.trim();
    }
    if (role === 'combobox') {
      const sel = el.options && el.options[el.selectedIndex];
      if (sel && sel.text) return sel.text.trim();
    }
    const txt = (el.innerText || el.value || el.getAttribute('name') || '').trim();
    return txt.slice(0, 120);
  };
  const nearby = (el) => {
    const out = [];
    // the label cell in a table-row layout is the previous cell; capture row text
    const cell = el.closest('td'); const row = el.closest('tr');
    if (row) out.push((row.innerText || '').trim().slice(0, 120));
    if (cell && cell.previousElementSibling) out.push((cell.previousElementSibling.innerText || '').trim());
    return out.filter(Boolean);
  };
  const els = Array.from(document.querySelectorAll(SEL));
  const results = [];
  let i = 0;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;              // skip invisibles
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    el.setAttribute('data-lb-idx', String(i));                  // stable handle for act()
    const role = roleFor(el);
    results.push({
      role,
      name: nameFor(el, role),
      value: ('value' in el) ? String(el.value ?? '') : null,
      checked: ('checked' in el) ? !!el.checked : null,
      box: [Math.round(r.x + base.x), Math.round(r.y + base.y), Math.round(r.width), Math.round(r.height)],
      nearby: nearby(el),
      lb_idx: i,
    });
    i++;
  }
  return results;
}
"""


class PlaywrightWebSurface:
    """A live headed Chromium as a surface-neutral Surface."""

    def __init__(self, start_url: str, *, headed: bool = True, width: int = 1280, height: int = 900) -> None:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not headed)
        self._context = self._browser.new_context(viewport={"width": width, "height": height})
        self._page: Page = self._context.new_page()
        self._viewport = Viewport(width=width, height=height, device_scale_factor=1.0)
        # (frame, local_index) for every element in the last observe(), keyed by global index.
        self._index: list[tuple[Frame, int]] = []
        self._page.goto(start_url, wait_until="networkidle")

    # ── perceive ──────────────────────────────────────────────────────────
    def observe(self) -> Observation:
        self._page.wait_for_load_state("domcontentloaded")
        elements: list[Element] = []
        self._index = []
        digest_parts: list[str] = []

        for frame in self._page.frames:
            base = self._frame_offset(frame)
            try:
                raw = frame.evaluate(_COLLECT_JS, base)
            except Exception:
                continue  # a frame may be mid-navigation; skip it this pass
            container = [] if frame == self._page.main_frame else [f"frame:{frame.name or frame.url}"]
            for item in raw:
                gi = len(elements)
                name = item["name"]
                if item.get("checked") is not None:
                    name = f"{name} ({'checked' if item['checked'] else 'unchecked'})".strip()
                elements.append(
                    Element(
                        index=gi,
                        role=item["role"],
                        name=name,
                        value=item["value"],
                        bbox=tuple(item["box"]),  # type: ignore[arg-type]
                        container_path=container,
                        nearby_text=item["nearby"],
                    )
                )
                self._index.append((frame, item["lb_idx"]))
                digest_parts.append(f"{'/'.join(container)}|{item['role']}|{item['name']}")

        aria_digest = hashlib.sha256("\n".join(digest_parts).encode()).hexdigest()[:16]
        screenshot = self._page.screenshot()
        return Observation(
            url=self._page.url,
            title=self._page.title(),
            elements=elements,
            screenshot_png=screenshot,
            aria_digest=aria_digest,
        )

    def _frame_offset(self, frame: Frame) -> dict[str, float]:
        """Top-left of a frame in top-level viewport coords, so child boxes can be shifted."""
        if frame == self._page.main_frame:
            return {"x": 0.0, "y": 0.0}
        el = frame.frame_element()
        box = el.bounding_box()
        return {"x": box["x"], "y": box["y"]} if box else {"x": 0.0, "y": 0.0}

    # ── act ───────────────────────────────────────────────────────────────
    def act(self, action: Action) -> ActionResult:
        try:
            if action.kind == "navigate":
                self._page.goto(action.value or "", wait_until="networkidle")
                return ActionResult(ok=True, settled=True)
            if action.kind == "press":
                self._page.keyboard.press(action.value or "Enter")
                self._settle()
                return ActionResult(ok=True, settled=True)

            if action.target_index is None:
                return ActionResult(ok=False, error="action requires a target_index", settled=True)
            frame, local = self._resolve(action.target_index)
            locator = frame.locator(f"[data-lb-idx='{local}']")

            if action.kind == "click":
                locator.click()
            elif action.kind == "type":
                locator.fill(action.value or "")
            elif action.kind == "select":
                locator.select_option(action.value or "")
            elif action.kind == "scroll":
                locator.scroll_into_view_if_needed()
            else:
                return ActionResult(ok=False, error=f"unknown action kind {action.kind}", settled=True)

            self._settle()
            return ActionResult(ok=True, settled=True)
        except Exception as exc:  # a hostile app throws plenty; surface it, don't crash the loop
            return ActionResult(ok=False, error=f"{type(exc).__name__}: {exc}", settled=False)

    def _resolve(self, global_index: int) -> tuple[Frame, int]:
        if global_index < 0 or global_index >= len(self._index):
            raise IndexError(f"element index {global_index} out of range (have {len(self._index)})")
        return self._index[global_index]

    def _settle(self) -> None:
        try:
            self._page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # not every action triggers navigation; a timeout here is fine

    # ── evidence ──────────────────────────────────────────────────────────
    def snapshot(self, dir: Path) -> None:
        dir.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(dir / "screenshot.png"))
        obs = self.observe()
        (dir / "aria.json").write_text(
            json.dumps([e.model_dump(exclude={"screenshot_png"}) for e in obs.elements], indent=2)
        )

    @property
    def viewport(self) -> Viewport:
        return self._viewport

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()

    def __enter__(self) -> "PlaywrightWebSurface":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
