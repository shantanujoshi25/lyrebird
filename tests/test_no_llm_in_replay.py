"""C5 — the no-LLM-in-replay import guard (A4).

The whole value proposition collapses if the LLM leaks into replay. This test enforces the
invariant structurally: import the replay package in a fresh interpreter and assert the
Anthropic SDK is NOT loaded. It runs in a subprocess so an already-imported anthropic (from
a discovery test in the same session) can't produce a false pass.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_replay_does_not_load_anthropic() -> None:
    code = (
        "import importlib, sys\n"
        "importlib.import_module('lyrebird.replay')\n"
        "importlib.import_module('lyrebird.replay.executor')\n"
        "importlib.import_module('lyrebird.replay.resolver')\n"
        "importlib.import_module('lyrebird.replay.detector')\n"
        # the LLM client module must not have been pulled in transitively
        "assert 'anthropic' not in sys.modules, 'anthropic SDK loaded by replay!'\n"
        "assert 'lyrebird.discovery.llm' not in sys.modules, 'LLM client loaded by replay!'\n"
        "print('clean')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"import guard failed:\n{result.stdout}\n{result.stderr}"
    assert "clean" in result.stdout
