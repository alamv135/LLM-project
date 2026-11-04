"""Local tools: company materials search, skill references, evidence recall."""

from __future__ import annotations

from typing import Any

from . import mirror
from .base import ToolContext, ToolError, envelope, string_arg


def search_company_docs(ctx: ToolContext, query: str, limit: int = 4) -> dict[str, Any]:
    scored = []
    for ch in ctx.chunks:
        sc = mirror.text_score(ch.text, query)
        if sc > 0:
            scored.append((sc, ch))
    scored.sort(key=lambda x: -x[0])
    recs = [{"chunk_id": c.chunk_id, "doc": c.doc, "location": c.location, "doc_date": c.doc_date,
             "text": c.text[:500]} for _, c in scored[:limit]]
    return {"source": "Company materials", "total": len(scored), "records": recs}


SEARCH_DOCS_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": string_arg("Keywords to find in the company's own materials"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 8},
    },
}
SEARCH_DOCS_OUTPUT = envelope({"type": "object", "required": ["chunk_id", "doc", "location", "text"]})


def read_skill_reference(ctx: ToolContext, skill: str, file: str) -> dict[str, Any]:
    """Staged reading (Lec 4 'Published Skill example'): load a reference file on demand."""
    if ctx.skills is None:
        raise ToolError("unavailable", "no skill library")
    text = ctx.skills.read_reference(skill, file)
    return {"source": f"skill:{skill}/{file}", "total": 1, "records": [{"file": file, "text": text[:3000]}]}


READ_REF_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["skill", "file"],
    "properties": {
        "skill": string_arg("Skill name from the skill index"),
        "file": string_arg("Reference file listed by the loaded skill, e.g. references/fda-pathways.md",
                           pattern="^[A-Za-z0-9_./-]+$"),
    },
}


def get_evidence(ctx: ToolContext, evidence_id: str) -> dict[str, Any]:
    """Recover full details of an evidence item that was compressed out of the active context
    (Lec 5 'Context compression': keep originals outside the prompt so they can be recovered)."""
    ev = ctx.evidence.get(evidence_id)
    if ev is None:
        raise ToolError("permanent", f"unknown evidence id {evidence_id}")
    return {"source": "evidence store", "total": 1,
            "records": [{"evidence_id": ev.evidence_id, "source": ev.source, "identifier": ev.identifier,
                         "url": ev.url, "fields": ev.fields, "independent": ev.independent}]}


GET_EVIDENCE_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["evidence_id"],
    "properties": {"evidence_id": string_arg("Evidence id such as E3", pattern="^E[0-9]+$")},
}
