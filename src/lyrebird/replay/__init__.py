"""replay/ — the deterministic, LLM-free production execution path.

CRITICAL INVARIANT (R3, A4): this package has NO import path to the LLM client. Replay
resolves locators, waits on conditions, verifies checkpoints, and extracts outputs without
ever asking a model to decide. `tests/test_no_llm_in_replay.py` imports this package and
asserts the Anthropic SDK is absent from sys.modules — enforcing the invariant structurally.
See docs/01_ARCHITECTURE.md §7.
"""

from lyrebird.replay.executor import ReplayEngine
from lyrebird.replay.resolver import ResolveError, resolve

__all__ = ["ReplayEngine", "ResolveError", "resolve"]
