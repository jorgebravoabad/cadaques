"""JSON Schema fragments and a small validator for tool arguments.

Tool inputs and outputs are described in JSON Schema (draft 2020-12),
the format agent runtimes and MCP clients already consume. Validation
uses the subset of keywords these schemas actually use, implemented on
the standard library so the agent layer adds no core dependency
(ADR-0008). Errors carry a JSON-pointer path, so an agent can fix the
offending field rather than guess.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from ..core.cost import CURRENCIES

CAMPAIGN_ID: dict[str, Any] = {
    "type": "string",
    "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$",
    "description": "Campaign identifier: lowercase letters, digits, '-' and '_'.",
}

REQUEST_ID: dict[str, Any] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "description": (
        "Caller-chosen idempotency key. Repeating a call with the same request_id "
        "and arguments returns the original response without acting twice."
    ),
}

ACTOR: dict[str, Any] = {
    "type": "object",
    "description": "Who is making this call. Recorded on every event the call causes.",
    "properties": {
        "kind": {"type": "string", "enum": ["human", "agent", "service"]},
        "id": {"type": "string", "minLength": 1, "description": "Person, agent or service name."},
        "model": {"type": "string", "description": "Model identifier; required when kind is 'agent'."},
        "version": {"type": "string", "description": "Model or software version."},
        "session": {"type": "string", "description": "Optional session or run identifier."},
    },
    "required": ["kind", "id"],
    "additionalProperties": False,
}

COST: dict[str, Any] = {
    "type": "object",
    "description": "Multi-currency cost; omitted currencies are zero.",
    "properties": {c: {"type": "number", "minimum": 0} for c in CURRENCIES},
    "additionalProperties": False,
}

PARAMS: dict[str, Any] = {
    "type": "object",
    "description": "Design variables, by name.",
    "additionalProperties": {"type": "number"},
}

FIDELITY: dict[str, Any] = {
    "type": "object",
    "description": "Fidelity knobs (e.g. resolution, lattice size), by name.",
    "additionalProperties": {"type": ["number", "string", "boolean"]},
}

_TYPES: dict[str, Any] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def validate(value: Any, schema: Mapping[str, Any], path: str = "") -> list[str]:
    """Return a list of ``"<json-pointer>: <problem>"`` messages; empty if valid."""
    errors: list[str] = []
    where = path or "/"

    expected = schema.get("type")
    if expected is not None:
        types = [expected] if isinstance(expected, str) else list(expected)
        if not any(_TYPES[t](value) for t in types):
            return [f"{where}: expected {' or '.join(types)}, got {type(value).__name__}"]
    if isinstance(value, float) and not math.isfinite(value):
        return [f"{where}: must be a finite number"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: must be one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: must be >= {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: must be <= {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: must have at least {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: must have at most {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: must match {schema['pattern']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{where}: must have at most {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], f"{path}/{i}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                errors.append(f"{path}/{name}: required")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in props:
                errors += validate(item, props[name], f"{path}/{name}")
            elif extra is False:
                errors.append(f"{path}/{name}: unexpected property")
            elif isinstance(extra, dict):
                errors += validate(item, extra, f"{path}/{name}")
    return errors
