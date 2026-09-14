"""check_action — the allowlist gate, called before every action in both loops (C6b).

Fail-closed at every step: a URL with no parseable host is denied; a host not in the
domain allowlist is denied; a path matching no allowed url_pattern is denied; an action
type not in the allowed set is denied. Only when all three pass is the action allowed.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from urllib.parse import urlparse

from lyrebird.policy.model import Policy


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def check_action(policy: Policy, *, url: str, action_type: str) -> Decision:
    if not url:
        return Decision(False, "empty url (fail-closed)")

    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"

    if host not in policy.allowlist.domains:
        return Decision(False, f"domain {host!r} not in allowlist")

    if not any(fnmatch.fnmatch(path, pat) for pat in policy.allowlist.url_patterns):
        return Decision(False, f"route {path!r} matches no allowed url pattern")

    if action_type not in policy.allowlist.action_types:
        return Decision(False, f"action type {action_type!r} not allowed")

    return Decision(True)
