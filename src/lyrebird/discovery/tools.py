"""Tool schemas the model may call during discovery.

The model works from a screenshot with numbered marks plus a compact element list, and
picks an element by `index`. The `type` tool is the load-bearing one for param binding
(A13): it accepts EITHER a literal `value` OR a `param` name. When the model types a
declared input, it references the param, and the recorder stores `{{param}}` rather than
the literal — so replay can substitute a fresh value and sensitive values never enter the
step. The prompt instructs the model to prefer `param` for any declared input.
"""

from __future__ import annotations

from typing import Any

TOOLS: list[dict[str, Any]] = [
    {
        "name": "observe",
        "description": "Re-observe the current screen. Returns a fresh screenshot and element list. "
                       "Call this after any action that changes the page.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "Click the interactable element with the given index from the latest observation.",
        "input_schema": {
            "type": "object",
            "properties": {"index": {"type": "integer", "description": "element index to click"}},
            "required": ["index"],
        },
    },
    {
        "name": "type",
        "description": "Type into the textbox with the given index. Provide EITHER `param` (the name of "
                       "a declared input — preferred whenever the field corresponds to one) OR a literal "
                       "`value`. Prefer `param` so the recorded flow is reusable and never stores real values.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer"},
                "param": {"type": "string", "description": "name of a declared input parameter to bind"},
                "value": {"type": "string", "description": "literal text (only when no param applies)"},
            },
            "required": ["index"],
        },
    },
    {
        "name": "select",
        "description": "Select an option in the dropdown (combobox) with the given index, by its value.",
        "input_schema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "value": {"type": "string"}},
            "required": ["index", "value"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the element with the given index into view.",
        "input_schema": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]},
    },
    {
        "name": "navigate",
        "description": "Navigate directly to a URL (must be within the allowed app).",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "press",
        "description": "Press a keyboard key (e.g. Enter) globally.",
        "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    },
    {
        "name": "finish",
        "description": "Declare the goal reached (success=true) or a dead end (success=false). "
                       "Include a short reason and, on success, the values you read for each declared output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "reason": {"type": "string"},
                "outputs": {"type": "object", "description": "declared output name -> observed value"},
            },
            "required": ["success"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}
