"""Export the Capability schema as JSON Schema.

Reviewability by a *calling agent* (R2) means the artifact's contract is machine-consumable:
an agent can read the JSON Schema to learn what inputs a capability needs and what outputs
it returns, without parsing Python. Pydantic gives us this for free via model_json_schema().
"""

from __future__ import annotations

import json
from pathlib import Path

from lyrebird.capability.schema import Capability


def export_schema() -> dict:
    """Return the Capability JSON Schema as a dict."""
    return Capability.model_json_schema()


def write_schema(path: Path) -> Path:
    """Write the JSON Schema to disk (used to commit artifacts/capability.schema.json)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(export_schema(), indent=2))
    return path
