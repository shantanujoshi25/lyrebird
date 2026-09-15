"""Same-session human-action capture that survives navigation (A3).

Naive page-injected listeners are wiped on every navigation. So we use TWO Playwright
mechanisms together:
  * context.expose_binding — installs a stable Python callback (`__lyrebird_capture`) that
    survives across page loads and frames;
  * context.add_init_script — re-injects the click/input listeners on EVERY document, so
    after any navigation the listeners reattach and call the exposed binding.

Captured events are appended to human_actions.jsonl in the run dir, redacted on the write
path (the human might type into a sensitive field). This records WHAT the human did during
their control of the same live session (R6), without a full co-browsing console.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lyrebird.policy import Policy, redact_text

_INIT_SCRIPT = r"""
() => {
  if (window.__lyrebird_installed) return;
  window.__lyrebird_installed = true;
  const send = (type, target) => {
    try {
      window.__lyrebird_capture({
        type,
        role: target.getAttribute ? (target.getAttribute('role') || target.tagName) : '',
        name: (target.getAttribute && target.getAttribute('name')) || (target.innerText || '').slice(0, 60),
        value: ('value' in target) ? String(target.value || '') : '',
        url: location.href,
      });
    } catch (e) {}
  };
  document.addEventListener('click', (e) => send('click', e.target), true);
  document.addEventListener('change', (e) => send('change', e.target), true);
}
"""


class HumanCapture:
    def __init__(self, context: Any, run_dir: Path | str, policy: Policy, *, sensitive_values: list[str] | None = None) -> None:
        self._context = context
        self._path = Path(run_dir) / "human_actions.jsonl"
        self._policy = policy
        self._sensitive = list(sensitive_values or [])
        self._installed = False

    def _record(self, source: Any, event: dict[str, Any]) -> None:
        # `source` is Playwright's binding source (page/frame); we only need the event dict.
        scrubbed = {
            k: redact_text(self._policy, str(v), sensitive_values=self._sensitive) if isinstance(v, str) else v
            for k, v in event.items()
        }
        with self._path.open("a") as fh:
            fh.write(json.dumps(scrubbed) + "\n")

    def start(self) -> None:
        """Attach the capture so it SURVIVES navigation.

        Three mechanisms together (belt and braces, because a page-level navigation does not
        reliably re-run a context init script across Playwright versions):
          1. expose_binding — a stable Python callback that persists across page loads;
          2. add_init_script — best-effort re-injection on future documents;
          3. a `load`/`framenavigated` handler that explicitly re-injects the listeners after
             every navigation, plus a one-time inject into pages already open now.
        """
        if self._installed:
            return
        self._context.expose_binding("__lyrebird_capture", self._record)
        self._context.add_init_script(_INIT_SCRIPT)

        def inject(target) -> None:
            try:
                target.evaluate(f"({_INIT_SCRIPT})()")
            except Exception:
                pass

        for page in self._context.pages:
            inject(page)                       # cover the already-loaded page
            page.on("load", inject)            # re-inject after each navigation
        # cover pages opened later in the session too
        self._context.on("page", lambda page: (inject(page), page.on("load", inject)))
        self._installed = True

    def actions(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text().splitlines() if line.strip()]
