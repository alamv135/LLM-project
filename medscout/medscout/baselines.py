"""Comparison systems from the proposal (§5.2).

direct : one model call over the company materials, no tools ("Direct LLM").
rag    : one fixed retrieval round (same public sources, company name / product as
         queries, no adaptation, no verification) followed by one synthesis call ("Standard RAG").

Both use the same model settings and the same verdict labels as MedScout, so
differences come from the agentic workflow (planning, adaptive tool use,
verification), not from the model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .intake import load_materials
from .llm import LLMError, call_model, make_llm
from .memory import Memory
from .protocol import ProtocolError, parse
from .skills import SkillLibrary
from .state import CATEGORIES, VERDICTS, RunState
from .tools import ToolContext, ToolGateway, build_specs
from .trace import Tracer
from .workflow import render

BASELINE_SCHEMA = {
    "type": "object", "required": ["findings"],
    "properties": {"findings": {"type": "array", "items": {
        "type": "object", "required": ["claim_text", "verdict"],
        "properties": {"claim_text": {"type": "string"}, "category": {"enum": CATEGORIES},
                       "verdict": {"enum": VERDICTS}, "evidence_ids": {"type": "array", "items": {"type": "string"}},
                       "rationale": {"type": "string"}, "conflicts": {"type": "array", "items": {"type": "string"}}}}}},
}


def run_baseline(settings, kind: str, company: str, product: str, materials: Path, case_dir: Path | None,
                 run_dir: Path) -> dict[str, Any]:
    st = RunState.new(company, str(materials), product)
    tracer = Tracer(run_dir / f"baseline-{kind}-{st.run_id}.jsonl", st.run_id)
    chunks, _ = load_materials(materials)
    st.chunks[:] = chunks
    llm = make_llm(settings)
    retrieved: list[dict[str, Any]] = []
    if kind == "rag":
        mirror = case_dir / "public_mirror" if case_dir and (case_dir / "public_mirror").exists() else None
        mem = Memory(settings.db_path)
        ctx = ToolContext(settings.backend, mirror, chunks=st.chunks, evidence=st.evidence,
                          skills=SkillLibrary(settings.skills_dir), cache=mem,
                          timeout_s=settings.limits.tool_timeout_s)
        gw = ToolGateway(build_specs(), ctx, st, settings.limits, tracer)
        fixed = [{"tool": "fda_510k_search", "arguments": {"applicant": company}},
                 {"tool": "fda_pma_search", "arguments": {"applicant": company}},
                 {"tool": "clinicaltrials_search", "arguments": {"sponsor": company}},
                 {"tool": "pubmed_search", "arguments": {"query": product or company}}]
        for i in range(0, len(fixed), 3):
            for o in gw.run_batch(fixed[i:i + 3], task_id="rag"):
                if o.ok:
                    retrieved += o.for_model(settings.limits.max_records_per_observation).get("records", [])
        gw.close()
        mem.close()
    budget_chars = (settings.limits.context_window - settings.limits.reserved_output) * 4 - 6000
    payload, used = [], 0
    for c in chunks:                       # truncate to the context budget (no batching in baselines)
        item = {"chunk_id": c.chunk_id, "doc": c.doc, "location": c.location, "text": c.text}
        used += len(json.dumps(item))
        if used > budget_chars:
            break
        payload.append(item)
    note = ("RETRIEVED_JSON holds public records retrieved once with the company and product name."
            if kind == "rag" else "No external sources are available; RETRIEVED_JSON is empty.")
    content = render((settings.prompts_dir / "baseline.md").read_text(), PHASE=f"baseline_{kind}",
                     COMPANY=company, PRODUCT=product, RETRIEVAL_NOTE=note,
                     CHUNKS_JSON=json.dumps(payload, ensure_ascii=False),
                     RETRIEVED_JSON=json.dumps(retrieved, ensure_ascii=False))
    msgs = [{"role": "system", "content": (settings.prompts_dir / "baseline_system.md").read_text()},
            {"role": "user", "content": content}]
    findings: list[dict[str, Any]] = []
    for _ in range(2):                     # one format repair, as for MedScout
        try:
            res = call_model(llm, msgs, state=st, tracer=tracer, settings=settings, purpose=f"baseline_{kind}",
                             schema=BASELINE_SCHEMA)
            findings = parse(res.text, BASELINE_SCHEMA)["findings"]
            break
        except ProtocolError as exc:
            st.counters.format_repairs += 1
            msgs = msgs + [{"role": "assistant", "content": res.text},
                           {"role": "user", "content": f"FORMAT_ERROR: {exc}. Return only the JSON object."}]
        except LLMError:
            break
    for f in findings:
        f.setdefault("evidence_ids", [])
        f.setdefault("conflicts", [])
    return {"system": kind, "findings": findings,
            "evidence": {k: {"identifier": e.identifier, "independent": e.independent} for k, e in st.evidence.items()},
            "counters": vars(st.counters), "summary": tracer.summary()}
