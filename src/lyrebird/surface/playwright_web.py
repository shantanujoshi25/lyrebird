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
  // Durable locators, read straight off the node — most-stable first. These are concrete
  // handles (a real id/name/attr, an ARIA role+name, exact text, or a tag ordinal), so replay
  // re-finds THIS node deterministically. We deliberately avoid class chains (brittle).
  const cssEscape = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/[^\w-]/g, '\\$&');
  const locatorsFor = (el, role, name) => {
    const locs = [];
    const tag = el.tagName.toLowerCase();
    // Surface the LABEL this cell COULD be anchored on (the previous cell's text). We don't
    // decide whether to use it — the LLM does at read_value, based on whether the value varies.
    if (tag === 'td' || tag === 'th') {
      const prev = el.previousElementSibling;
      const label = prev && (prev.innerText || '').trim();
      if (label) locs.push({ kind: 'label', value: label.slice(0, 60) });
    }
    if (el.id) locs.push({ kind: 'css', value: '#' + cssEscape(el.id) });
    const nm = el.getAttribute('name');
    if (nm) locs.push({ kind: 'css', value: tag + '[name="' + nm + '"]' });
    for (const a of el.attributes || []) {              // stable data-*/test hooks
      if (/^data-(test|testid|qa|id|cy)/.test(a.name) && a.value) {
        locs.push({ kind: 'css', value: tag + '[' + a.name + '="' + a.value + '"]' });
      }
    }
    if (role && name) locs.push({ kind: 'role', value: role, name: name.slice(0, 80) });
    if (name && (role === 'link' || role === 'button')) locs.push({ kind: 'text', value: name.slice(0, 80) });
    // last resort: tag + ordinal among same-tag siblings across the doc (stable if DOM is stable)
    const same = Array.from(document.getElementsByTagName(tag));
    const ord = same.indexOf(el);
    if (ord >= 0) locs.push({ kind: 'nth', value: tag + '@' + ord });
    return locs;
  };
  // interactables (what the model acts on) PLUS value-bearing text cells (what the model
  // READS). The latter are table cells holding a number/currency that aren't themselves
  // interactable — so an output value like a balance becomes an addressable element the
  // model can point at with read_value, and replay can re-resolve deterministically.
  const MONEY = /\$?\d[\d,]*\.\d{2}\b/;
  const interactables = Array.from(document.querySelectorAll(SEL));
  const cells = Array.from(document.querySelectorAll('td,th')).filter((c) => {
    if (c.querySelector(SEL)) return false;                     // skip cells that contain a control
    const t = (c.innerText || '').trim();
    return t && t.length <= 40 && MONEY.test(t);                // short, value-bearing text
  });
  const els = interactables.concat(cells);
  const results = [];
  let i = 0;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;              // skip invisibles
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') continue;
    el.setAttribute('data-lb-idx', String(i));                  // stable handle for act()
    const isCell = (el.tagName === 'TD' || el.tagName === 'TH') && !el.querySelector(SEL);
    const role = isCell ? 'text' : roleFor(el);
    const value = isCell ? (el.innerText || '').trim()
                         : (('value' in el) ? String(el.value ?? '') : null);
    const nm = isCell ? (el.innerText || '').trim().slice(0, 120) : nameFor(el, role);
    results.push({
      role,
      name: nm,
      value,
      checked: (!isCell && 'checked' in el) ? !!el.checked : null,
      box: [Math.round(r.x + base.x), Math.round(r.y + base.y), Math.round(r.width), Math.round(r.height)],
      nearby: nearby(el),
      locators: locatorsFor(el, role, nm),
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
        self._goto(start_url)

    def _goto(self, url: str) -> None:
        """Navigate robustly. `domcontentloaded` fires reliably even on chatty real sites
        (analytics/carousels keep the network busy so `networkidle` may never fire within the
        timeout); then we settle on network-idle BEST-EFFORT with a short cap. This avoids the
        hard 30s hang a strict `networkidle` goto causes on busy sites — a general fix, no
        per-site config."""
        self._page.goto(url, wait_until="domcontentloaded")
        try:
            self._page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

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
            is_main = frame == self._page.main_frame
            frame_id = None if is_main else (frame.name or frame.url)
            container = [] if is_main else [f"frame:{frame_id}"]
            for item in raw:
                gi = len(elements)
                name = item["name"]
                # checked-state suffix only makes sense for toggles; a text input also has a
                # (false) .checked property in JS, so gate on the role, not on presence.
                if item["role"] in ("checkbox", "radio") and item.get("checked") is not None:
                    name = f"{name} ({'checked' if item['checked'] else 'unchecked'})".strip()
                locators = item.get("locators") or []
                if frame_id:  # stamp the frame on each candidate so replay resolves in the right frame
                    for loc in locators:
                        loc["frame"] = frame_id
                elements.append(
                    Element(
                        index=gi,
                        role=item["role"],
                        name=name,
                        value=item["value"],
                        bbox=tuple(item["box"]),  # type: ignore[arg-type]
                        container_path=container,
                        nearby_text=item["nearby"],
                        locators=locators,
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
                self._goto(action.value or "")
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

    # ── durable-locator resolution (replay) ────────────────────────────────
    def _frame_for(self, frame_id: str | None) -> Frame:
        """Find the frame a locator lives in. `frame_id` is either a frame name/exact URL, or a
        STABLE SUBSTRING of the URL (the LLM's frame_anchor) — so a per-run iframe like
        .../workspace/member/100003 is matched by 'workspace/member'. Exact match wins; else the
        first frame whose name/url contains the anchor; else the main frame."""
        if not frame_id:
            return self._page.main_frame
        for f in self._page.frames:
            if f.name == frame_id or f.url == frame_id:
                return f
        for f in self._page.frames:
            if frame_id in (f.url or "") or frame_id in (f.name or ""):
                return f
        return self._page.main_frame  # frame gone/renamed -> fall back to main (resolution may still fail cleanly)

    def _candidate_locator(self, frame: Frame, cand: dict):
        """A Playwright Locator for one durable candidate, or None if the kind is unusable."""
        kind = cand.get("kind")
        val = cand.get("value") or ""
        if kind == "css":
            return frame.locator(val)
        if kind == "role":
            name = cand.get("name")
            return frame.get_by_role(val, name=name) if name else frame.get_by_role(val)  # type: ignore[arg-type]
        if kind == "text":
            return frame.get_by_text(val, exact=True)
        if kind == "label":
            # the value cell anchored on a stable label: the cell immediately AFTER the cell
            # whose exact text is `val`, within the same row. Tag-agnostic via CSS adjacency
            # on the row's cells; falls back to any element following the label's cell.
            row = frame.locator("tr", has=frame.get_by_role("cell", name=val, exact=True))
            if row.count() >= 1:
                cells = row.first.get_by_role("cell")
                n = cells.count()
                for i in range(n):  # find the label cell, return the next one
                    if (cells.nth(i).inner_text() or "").strip() == val and i + 1 < n:
                        return cells.nth(i + 1)
            return None
        if kind == "nth":
            tag, _, ord_s = val.partition("@")
            try:
                return frame.locator(tag).nth(int(ord_s))
            except ValueError:
                return None
        return None

    def resolve_locator(self, locator: object) -> tuple[object | None, int]:
        """Resolve a DurableLocator to a unique Playwright Locator. Returns (locator, depth):
        the first candidate that resolves to exactly one visible element wins; `depth` is its
        index (0 = primary still works; higher = drift). (None, -1) if nothing resolves."""
        candidates = getattr(locator, "candidates", []) or []
        for depth, cand in enumerate(candidates):
            c = cand.model_dump() if hasattr(cand, "model_dump") else dict(cand)
            frame = self._frame_for(c.get("frame"))
            pw = self._candidate_locator(frame, c)
            if pw is None:
                continue
            try:
                if pw.count() == 1:
                    return pw, depth
            except Exception:
                continue
        return None, -1

    def read_locator(self, locator: object) -> tuple[str | None, int]:
        """Resolve `locator` and read its display text (input value if it's a field). Returns
        (text, depth) or (None, -1) if unresolved."""
        pw, depth = self.resolve_locator(locator)
        if pw is None:
            return None, -1
        try:
            tag = (pw.evaluate("e => e.tagName.toLowerCase()") or "")
            if tag in ("input", "textarea", "select"):
                return (pw.input_value() or "").strip(), depth
            return (pw.inner_text() or "").strip(), depth
        except Exception:
            return None, depth

    def act_locator(self, kind: str, locator: object, value: str | None) -> ActionResult:
        """Perform an action against a DurableLocator (the replay act path — no element index)."""
        pw, _ = self.resolve_locator(locator)
        if pw is None:
            return ActionResult(ok=False, error="LOCATOR_NOT_FOUND", settled=True)
        try:
            if kind == "click":
                pw.click()
            elif kind == "type":
                pw.fill(value or "")
            elif kind == "select":
                pw.select_option(value or "")
            elif kind == "scroll":
                pw.scroll_into_view_if_needed()
            else:
                return ActionResult(ok=False, error=f"unknown action kind {kind}", settled=True)
            self._settle()
            return ActionResult(ok=True, settled=True)
        except Exception as exc:
            return ActionResult(ok=False, error=f"{type(exc).__name__}: {exc}", settled=False)

    def page_text(self) -> str:
        """Visible innerText across the main frame and every iframe, joined.

        Text conditions and output extraction read display text (e.g. the balance in the
        workspace iframe), which the interactable-only collector doesn't capture.
        """
        parts: list[str] = []
        for frame in self._page.frames:
            try:
                parts.append(frame.evaluate("() => document.body ? document.body.innerText : ''"))
            except Exception:
                continue
        return "\n".join(p for p in parts if p)

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

    def __enter__(self) -> PlaywrightWebSurface:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
