"""Capability store — versioned, immutable JSON on disk.

Versioned + reviewable by a human (R2/A6): artifacts are plain JSON at a deterministic,
per-version path (`<capability_id>/v<version>.json`), so each version is immutable and git
gives us history for free — no database needed. Loading validates against the schema, so a
corrupt or drifted artifact fails loudly rather than replaying garbage.
"""

from __future__ import annotations

from pathlib import Path

from lyrebird.capability.schema import Capability


class CapabilityStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _path(self, capability_id: str, version: int) -> Path:
        return self.root / capability_id / f"v{version}.json"

    def save(self, cap: Capability) -> Path:
        path = self._path(cap.capability_id, cap.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        # indent for human review; this is a reviewable artifact, not a hot path.
        path.write_text(cap.model_dump_json(indent=2))
        return path

    def load(self, capability_id: str, version: int) -> Capability:
        path = self._path(capability_id, version)
        if not path.exists():
            raise FileNotFoundError(f"no capability {capability_id} v{version} at {path}")
        return Capability.model_validate_json(path.read_text())
