"""evidence/ — the run-directory sink: redacted JSONL, screenshots, ARIA snapshots.

Everything written here passes through policy redaction on the way in, so no unredacted
sensitive value ever reaches disk (R4/A7). This module is a pure sink — it holds no
business logic and calls neither the LLM nor the surface directly.
See docs/01_ARCHITECTURE.md §9 for the per-run layout.
"""

from lyrebird.evidence.writer import EvidenceWriter

__all__ = ["EvidenceWriter"]
