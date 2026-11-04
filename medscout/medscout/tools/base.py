"""Tool contract (Lec 4 "Tool contract", Lec 5 "Tool definition").

A tool exposes an executable operation through a name, an input contract and
a result. The model only sees the name, description and input schema; the
runtime validates inputs and outputs and enforces execution conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class ToolError(Exception):
    """Structured tool failure.

    kind:
      transient   – timeout / rate limit / 5xx: bounded retry may help
      permanent   – bad arguments / 4xx: do not repeat unchanged
      unavailable – source not reachable in this configuration (e.g. no offline mirror)
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class ToolContext:
    """Everything a handler may read. Handlers never see the model or the prompt."""
    backend: str                       # "mirror" | "live"
    mirror_dir: Path | None            # data/cases/<case>/public_mirror
    chunks: list[Any] = field(default_factory=list)          # SourceChunk list (company materials)
    evidence: dict[str, Any] = field(default_factory=dict)   # evidence store (read-only for tools)
    skills: Any = None                 # SkillLibrary
    cache: Any = None                  # memory.Memory (HTTP cache)
    timeout_s: float = 10.0


@dataclass
class ToolSpec:
    name: str
    description: str                   # when to use it — the model reads this
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    source_label: str                  # shown in the source log
    independent: bool = True           # False: evidence is company-reported
    record_id_field: str = ""          # field in each record used as the evidence identifier
    url_template: str = ""             # e.g. "https://clinicaltrials.gov/study/{nct_id}"
    read_only: bool = True             # all MVP tools are reads; writes need idempotency keys
    registers_evidence: bool = True

    def model_view(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.input_schema}


RESULT_ENVELOPE: dict[str, Any] = {
    "type": "object",
    "required": ["source", "total", "records"],
    "properties": {
        "source": {"type": "string"},
        "total": {"type": "integer", "minimum": 0},
        "records": {"type": "array", "items": {"type": "object"}},
        "note": {"type": "string"},
        "synthetic": {"type": "boolean"},
    },
}


def envelope(records_schema: dict[str, Any]) -> dict[str, Any]:
    env = {**RESULT_ENVELOPE, "properties": {**RESULT_ENVELOPE["properties"]}}
    env["properties"]["records"] = {"type": "array", "items": records_schema}
    return env


def string_arg(desc: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, "minLength": 1, "maxLength": 200, **extra}
