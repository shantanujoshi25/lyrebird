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
        "description": "Click the interactable element with the given index from the latest observation. "
                       "Include `expect_text`: a short, distinctive piece of text you expect to appear after "
                       "this click succeeds (e.g. a heading on the next page). It becomes the step's recorded "
                       "postcondition so deterministic replay can verify the click worked.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "element index to click"},
                "expect_text": {"type": "string", "description": "text you expect on the page after this action"},
            },
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
                "expect_text": {"type": "string", "description": "optional: text expected after typing (usually not needed)"},
            },
            "required": ["index"],
        },
    },
    {
        "name": "select",
        "description": "Select an option in the dropdown (combobox) with the given index, by its value.",
        "input_schema": {
            "type": "object",
            "properties": {"index": {"type": "integer"}, "value": {"type": "string"},
                           "expect_text": {"type": "string", "description": "optional: text expected after selecting"}},
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
        "description": "Navigate directly to a URL (must be within the allowed app). Include `expect_text`: "
                       "a short text you expect on the destination page, recorded as the step's postcondition.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"},
                           "expect_text": {"type": "string", "description": "text expected on the destination page"}},
            "required": ["url"],
        },
    },
    {
        "name": "press",
        "description": "Press a keyboard key (e.g. Enter) globally. Include `expect_text` for the postcondition.",
        "input_schema": {
            "type": "object",
            "properties": {"key": {"type": "string"},
                           "expect_text": {"type": "string", "description": "text expected after the keypress"}},
            "required": ["key"],
        },
    },
    {
        "name": "read_value",
        "description": "Record which on-screen element holds a declared OUTPUT's value, so replay can re-read "
                       "the SAME element deterministically (no LLM). Point at the element by `index` from the "
                       "latest observation.\n"
                       "  YOUR judgment matters here — decide whether this value is VARIABLE or FIXED across runs:\n"
                       "    • variable: it changes when the inputs change (a balance, an order total, a name that "
                       "depends on the member/product). Then you MUST give `anchor_label` — a STABLE nearby label "
                       "the value sits next to (e.g. 'Savings', 'Balance', 'Total'). Replay finds the value by that "
                       "label, so it still works when the value itself differs. NEVER rely on the value's own text.\n"
                       "    • fixed: it's the same on every run (a static field, a constant). Then no anchor is "
                       "needed; replay can match the element directly.\n"
                       "  value_seen: the EXACT value visible now (ground truth for a replay sanity-check).\n"
                       "  frame_anchor: if the value lives inside an iframe whose URL contains an input value "
                       "(e.g. .../workspace/member/100001), give a STABLE substring of that URL that will hold "
                       "across runs (e.g. 'workspace/member'). Replay matches the iframe by this substring so it "
                       "still finds the value when the member/id changes. Omit if the value isn't in such a frame.\n"
                       "Call once per declared output before finish.",
        "input_schema": {
            "type": "object",
            "properties": {
                "output": {"type": "string", "description": "name of the declared output"},
                "index": {"type": "integer", "description": "index (from the latest observation) of the element showing the value"},
                "value_seen": {"type": "string", "description": "the exact value visible on screen now"},
                "value_stability": {"type": "string", "enum": ["variable", "fixed"],
                                    "description": "does this value change across runs? YOU decide"},
                "anchor_label": {"type": "string",
                                 "description": "REQUIRED if variable: the stable nearby label the value is anchored to (e.g. 'Savings')"},
                "frame_anchor": {"type": "string",
                                 "description": "optional: a stable substring of the iframe URL the value lives in (e.g. 'workspace/member'), if that URL varies per run"},
            },
            "required": ["output", "index", "value_seen", "value_stability"],
        },
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
