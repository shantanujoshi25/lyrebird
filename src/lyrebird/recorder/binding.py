"""Param binding (A13).

Primary path: discovery already typed a declared input via `param`, so the trajectory step
carries an explicit "{{param}}" binding — the recorder uses it directly. Fallback path: the
model typed a raw literal that happens to equal a known example value; we bind it to that
param but FLAG it inferred, so a human reviewer can confirm rather than trusting a silent
guess. Sensitive values are never matched by literal (their examples aren't stored).
"""

from __future__ import annotations

from dataclasses import dataclass

from lyrebird.capability.schema import InputParam


@dataclass
class Binding:
    value: str            # what to store in the step: "{{param}}" or the literal
    inferred: bool = False  # True when derived by value->param matching (needs human review)


def bind_value(
    *,
    explicit_binding: str | None,
    literal: str | None,
    inputs: list[InputParam],
) -> Binding | None:
    """Return the Binding for a typed step, or None if there's nothing to bind."""
    # Primary: discovery gave us an explicit "{{param}}" binding.
    if explicit_binding and explicit_binding.startswith("{{"):
        return Binding(value=explicit_binding, inferred=False)

    # Fallback: a raw literal that exactly matches a non-sensitive param's example.
    if literal:
        for p in inputs:
            if not p.sensitive and p.example is not None and literal == p.example:
                return Binding(value=f"{{{{{p.name}}}}}", inferred=True)
        # a literal with no matching param: keep it as-is (it's a constant in the flow)
        return Binding(value=literal, inferred=False)

    return None
