"""Output contracts between the model and the runtime.

Lec 4 "Naive JSON tool protocol" / Lec 5 "Validation and call identifiers":
the model proposes; the runtime parses, validates the shape, checks the
allowed operations and only then executes. A valid shape can still contain
the wrong decision — meaning is tested separately (see verify.py, eval/).
"""

from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

from .state import CATEGORIES, VERDICTS


class ProtocolError(ValueError):
    """Model output did not satisfy the contract. Message is shown to the model on repair."""


# ---------------------------------------------------------------- phase 1: claims
EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claims"],
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "maxItems": 25,
            "items": {
                "type": "object",
                "required": ["text", "category", "source_chunk_ids"],
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string", "minLength": 8},
                    "category": {"enum": CATEGORIES},
                    "source_chunk_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "product": {"type": "string"},
                    "status_language": {"type": "string"},
                    "materiality": {"enum": ["high", "medium", "low"]},
                },
            },
        }
    },
}

# ---------------------------------------------------------------- phase 2: plan
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["tasks"],
    "additionalProperties": False,
    "properties": {
        "tasks": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "required": ["claim_ids", "question", "skill", "priority"],
                "additionalProperties": False,
                "properties": {
                    "claim_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                    "question": {"type": "string", "minLength": 8},
                    "skill": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 1, "maximum": 3},
                },
            },
        }
    },
}

# ---------------------------------------------------------------- phase 3: research loop
_CALL = {
    "type": "object",
    "required": ["tool", "arguments"],
    "additionalProperties": False,
    "properties": {"tool": {"type": "string"}, "arguments": {"type": "object"}},
}

ASSESSMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["claim_id", "verdict", "evidence_ids", "rationale"],
    "additionalProperties": False,
    "properties": {
        "claim_id": {"type": "string"},
        "verdict": {"enum": VERDICTS},
        "evidence_ids": {"type": "array", "items": {"type": "string", "pattern": "^E[0-9]+$"}},
        "rationale": {"type": "string", "minLength": 10},
        "conflicts": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "identity_confirmed": {"type": ["boolean", "null"]},
    },
}

DECISION_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {
            "type": "object",
            "required": ["action", "calls"],
            "additionalProperties": False,
            "properties": {
                "action": {"const": "tool_calls"},
                "thought": {"type": "string"},
                "calls": {"type": "array", "minItems": 1, "maxItems": 3, "items": _CALL},
            },
        },
        {
            "type": "object",
            "required": ["action", "assessments"],
            "additionalProperties": False,
            "properties": {
                "action": {"const": "finish"},
                "thought": {"type": "string"},
                "assessments": {"type": "array", "minItems": 1, "items": ASSESSMENT_SCHEMA},
            },
        },
        {
            "type": "object",
            "required": ["action", "question"],
            "additionalProperties": False,
            "properties": {
                "action": {"const": "ask_user"},
                "thought": {"type": "string"},
                "question": {"type": "string", "minLength": 5},
                "options": {"type": "array", "items": {"type": "string"}},
                # name the runtime may store as a confirmed alias if the human answers "yes"
                "alias_candidate": {"type": "string"},
            },
        },
    ]
}

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(raw: str) -> Any:
    """Parse model text as JSON. Tolerates code fences and leading prose
    (Lec 5 "Function calls in text": find the call in surrounding prose)."""
    if raw is None:
        raise ProtocolError("empty model output")
    text = _FENCE.sub("", raw.strip()).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ProtocolError("output is not JSON: return exactly one JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON ({exc.msg} at char {exc.pos}): return exactly one JSON object") from exc


def _validate(obj: Any, schema: dict[str, Any]) -> None:
    try:
        jsonschema.validate(obj, schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ProtocolError(f"schema violation at {path}: {exc.message[:300]}") from exc


def parse(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    obj = extract_json(raw)
    if not isinstance(obj, dict):
        raise ProtocolError("top-level value must be a JSON object")
    _validate(obj, schema)
    return obj


def parse_decision(raw: str, allowed_tools: set[str], max_calls: int) -> dict[str, Any]:
    """Shape + allowlist checks for one research-loop decision."""
    obj = extract_json(raw)
    if not isinstance(obj, dict):
        raise ProtocolError("top-level value must be a JSON object")
    action = obj.get("action")
    if action not in {"tool_calls", "finish", "ask_user"}:
        raise ProtocolError('"action" must be one of "tool_calls", "finish", "ask_user"')
    _validate(obj, DECISION_SCHEMA)
    if action == "tool_calls":
        if len(obj["calls"]) > max_calls:
            raise ProtocolError(f"at most {max_calls} calls per decision")
        for call in obj["calls"]:
            if call["tool"] not in allowed_tools:
                raise ProtocolError(f'unknown tool "{call["tool"]}"; allowed: {sorted(allowed_tools)}')
    return obj
