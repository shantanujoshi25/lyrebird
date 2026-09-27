"""Infer the typed input/output contract from a natural-language goal (R3 redesign).

The interactive entry point takes only a goal + URL. The per-invocation *inputs* (e.g. an
account id) and *outputs* (e.g. the balance) are inferred from the goal by one cheap LLM
call, then confirmed by the user before discovery runs — a human review point (brief 3.2
"reviewable") and the safeguard against binding a constant as a parameter. No per-site script.

Credentials are handled separately (they're sensitive; never in the goal): a login flow adds
`username`/`password` inputs, password marked sensitive.
"""

from __future__ import annotations

from dataclasses import dataclass

from lyrebird.capability.schema import InputParam, OutputSpec
from lyrebird.discovery.llm import LLMClient

_CONTRACT_TOOL = [{
    "name": "declare_contract",
    "description": "Declare the typed input parameters and outputs implied by the goal. Inputs are "
                   "the per-invocation values a caller would supply (e.g. an account id); outputs are the "
                   "values to read and return (e.g. a balance). Do NOT include credentials here.",
    "input_schema": {
        "type": "object",
        "properties": {
            "inputs": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "type": {"type": "string", "enum": ["string", "integer", "boolean"]},
                "example": {"type": "string", "description": "an obviously-fake example value from the goal, if any"},
            }, "required": ["name", "type"]}},
            "outputs": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "type": {"type": "string", "enum": ["string", "integer", "number", "boolean"]},
                "shape": {"type": "string", "enum": ["currency", "number", "text"]},
            }, "required": ["name", "type"]}},
        },
        "required": ["inputs", "outputs"],
    },
}]

_SYSTEM = ("You translate a natural-language automation goal into a typed contract. Identify the "
           "per-invocation INPUTS (values that vary per run) and the OUTPUTS (values to read back). "
           "Exclude credentials. Call declare_contract exactly once.")


@dataclass
class Contract:
    inputs: list[InputParam]
    outputs: list[OutputSpec]


def infer_contract(llm: LLMClient, goal: str, *, needs_login: bool) -> Contract:
    """One LLM call: goal -> typed inputs/outputs. Adds login params if needs_login."""
    call = llm.decide(system=_SYSTEM,
                      messages=[{"role": "user", "content": f"GOAL: {goal}\nDeclare the contract."}],
                      tools=_CONTRACT_TOOL)
    data = call.input if call.name == "declare_contract" else {"inputs": [], "outputs": []}

    inputs: list[InputParam] = []
    if needs_login:
        inputs.append(InputParam(name="username", type="string", example="user"))
        inputs.append(InputParam(name="password", type="string", sensitive=True))
    for i in data.get("inputs", []):
        name = str(i.get("name", "")).strip()
        if not name or name in {"username", "password"}:
            continue
        inputs.append(InputParam(name=name, type=i.get("type", "string"), example=i.get("example") or None))

    outputs: list[OutputSpec] = []
    for o in data.get("outputs", []):
        name = str(o.get("name", "")).strip()
        if not name:
            continue
        # The extraction expression is authored at discovery (read_value); the contract only
        # declares the output's name and type. Replay casts the extracted value to this type.
        outputs.append(OutputSpec(name=name, type=o.get("type", "string")))
    return Contract(inputs=inputs, outputs=outputs)


def describe(contract: Contract) -> str:
    """A one-line human-readable summary for the confirmation prompt."""
    ins = ", ".join(f"{p.name}:{p.type}{'(sensitive)' if p.sensitive else ''}" for p in contract.inputs) or "(none)"
    outs = ", ".join(f"{o.name}:{o.type}" for o in contract.outputs) or "(none)"
    return f"inputs=[{ins}]  outputs=[{outs}]"
