"""Render the Investment Readiness Brief (proposal Fig. 1, step 6).

Deterministic rendering from the verified research record: every statement
in the brief maps to a claim, a finding and evidence ids in the source log.
"""

from __future__ import annotations

from collections import Counter

from .state import THEMES, VERDICTS, RunState

LABEL = {
    "publicly_supported": "Publicly supported",
    "partially_supported": "Partially supported",
    "possibly_contradicted": "Possibly contradicted",
    "company_reported_only": "Company-reported only",
    "unresolved": "Unresolved",
}


def _cite(st: RunState, eids: list[str]) -> str:
    return " ".join(f"[{e}]" for e in eids if e in st.evidence)


def _evidence_line(st: RunState, eid: str) -> str:
    e = st.evidence[eid]
    f = e.fields
    if e.tool == "fda_510k_search":
        desc = f"{f.get('k_number')} — {f.get('applicant')}, “{f.get('device_name')}”, {f.get('decision_description', '')} {f.get('decision_date', '')}"
    elif e.tool == "fda_pma_search":
        desc = f"{f.get('pma_number')} — {f.get('applicant')}, {f.get('trade_name')}, {f.get('decision_date', '')}"
    elif e.tool == "fda_classification_lookup":
        desc = f"product code {f.get('product_code')} — {f.get('device_name')}, class {f.get('device_class')}"
    elif e.tool == "clinicaltrials_search":
        desc = (f"{f.get('nct_id')} — “{f.get('title')}”, status {f.get('overall_status')}, enrollment "
                f"{f.get('enrollment')} ({str(f.get('enrollment_type', '')).lower()}), last update {f.get('last_update')}")
    elif e.tool == "pubmed_search":
        desc = f"PMID {f.get('pmid')} — “{f.get('title')}”, {f.get('journal')} {f.get('pub_date')}"
    else:
        desc = f"{f.get('doc')}, {f.get('location')}"
    tags = [] if e.independent else ["company-reported"]
    if e.synthetic:
        tags.append("SYNTHETIC")
    return f"- [{eid}] {e.source}: {desc}" + (f" _({', '.join(tags)})_" if tags else "")


def render(st: RunState, summary: dict) -> str:
    cfg = st.config
    synthetic = any(e.synthetic for e in st.evidence.values())
    L: list[str] = []
    title = st.company + (f" — {st.product}" if st.product else "")
    L += [f"# Investment Readiness Brief: {title}", "",
          f"Run `{st.run_id}` · started {st.started_at} · model `{cfg.get('provider')}/{cfg.get('model')}` · "
          f"data backend `{cfg.get('backend')}` · tools via `{cfg.get('tool_transport')}`"
          + (f" · focus: {', '.join(st.focus)}" if st.focus else ""), "",
          "> Preliminary, evidence-grounded first-pass screen for specialist review. Not investment, clinical, "
          "engineering or regulatory advice. Verdicts describe what the retrieved evidence does and does not establish."]
    if synthetic:
        L += ["> **SYNTHETIC DATA:** some evidence came from the offline synthetic mirror of a fictional case. "
              "It does not describe real FDA, registry or literature records."]
    if cfg.get("provider") == "mock":
        L += ["> **MOCK MODEL:** decisions were produced by the scripted mock policy, not an LLM."]
    L.append("")

    counts = Counter(f.verdict for f in st.findings.values() if f.task_id != "out_of_scope")
    L += ["## Snapshot", "", "| Verdict | Claims |", "|---|---|"]
    L += [f"| {LABEL[v]} | {counts.get(v, 0)} |" for v in VERDICTS]
    L += ["", f"{len(st.claims)} claims extracted from {len({c.doc for c in st.chunks})} documents; "
              f"{len(st.evidence)} evidence items retained; {len({q['question'] for q in st.review_questions})} questions for specialist review.", ""]

    sections = [("1. Product evidence", THEMES["product_evidence"]),
                ("2. Competitive and public commercial context", THEMES["competitive_commercial"])]
    for heading, cats in sections:
        claims = [c for c in st.claims if c.category in cats and st.findings.get(c.claim_id)
                  and st.findings[c.claim_id].task_id != "out_of_scope"]
        L += [f"## {heading}", ""]
        if not claims:
            L += ["_No claims in this theme._", ""]
        for c in claims:
            f = st.findings[c.claim_id]
            srcs = "; ".join(f"{ch.doc}, {ch.location}" for i in c.source_chunk_ids if (ch := st.chunk(i)))
            L += [f"### {c.claim_id} · {c.category} · {LABEL[f.verdict]}", "",
                  f"**Claim (company-reported):** “{c.text}”  ", f"_Source: {srcs}_", "",
                  f"**Assessment:** {f.rationale}"
                  + ("" if f.verified else " _(failed runtime verification — downgraded)_"), ""]
            ev = [e for e in f.evidence_ids if e in st.evidence]
            if ev:
                L += ["**Evidence:**", *[_evidence_line(st, e) for e in ev], ""]
            if f.conflicts:
                L += ["**Possible conflicts:**", *[f"- {x}" for x in f.conflicts], ""]
            if f.identity_checked and f.verdict in ("publicly_supported", "partially_supported"):
                L += ["**Entity match:** " + ("confirmed by reviewer / exact name match." if f.identity_confirmed
                                              else "not confirmed by a reviewer."), ""]
            if f.open_questions:
                L += ["**Open questions:**", *[f"- {q}" for q in f.open_questions], ""]

    L += ["## 3. Cross-document consistency", ""]
    if st.cross_claim_conflicts:
        for x in st.cross_claim_conflicts:
            vals = "; ".join(f"{v['value']} in {', '.join(v['sources'])}" for v in x["values"])
            L.append(f"- **{x['metric']}** differs across company materials: {vals}")
    else:
        L.append("_No numeric inconsistencies detected across company materials._")
    L.append("")

    L += ["## 4. Questions requiring specialist attention", ""]
    grouped: dict[str, list[str]] = {}
    for q in st.review_questions:
        ref = q.get("claim_id") or q.get("task_id") or "?"
        refs = grouped.setdefault(q["question"], [])
        if ref not in refs:
            refs.append(ref)
    for n, (question, refs) in enumerate(grouped.items(), 1):
        L.append(f"{n}. ({', '.join(refs)}) {question}")
    if not grouped:
        L.append("_None._")
    L.append("")

    oos = [c for c in st.claims if st.findings.get(c.claim_id) and st.findings[c.claim_id].task_id == "out_of_scope"]
    if oos:
        L += ["## 5. Out of scope for this request", "", *[f"- {c.claim_id} ({c.category}): {c.text}" for c in oos], ""]

    L += ["## Source log", "", "| ID | Source | Identifier | Retrieved | Independent | Synthetic | Link / location |",
          "|---|---|---|---|---|---|---|"]
    for e in st.evidence.values():
        L.append(f"| {e.evidence_id} | {e.source} | {e.identifier} | {e.retrieved_at} | "
                 f"{'yes' if e.independent else 'no'} | {'yes' if e.synthetic else 'no'} | {e.url} |")
    c = st.counters
    L += ["", "## Run log", "",
          f"- Model calls: {c.model_calls} · tool calls: {c.tool_calls} (errors: {c.tool_errors}) · "
          f"format repairs: {c.format_repairs} · verification rejections: {c.verify_rejections}",
          f"- Tokens in/out: {c.tokens_in}/{c.tokens_out}"
          + (" (estimated)" if cfg.get("provider") == "mock" else "") + f" · estimated cost: ${c.cost_usd:.4f}",
          f"- Model latency p50/p95: {summary.get('model_latency_p50_ms')}/{summary.get('model_latency_p95_ms')} ms · "
          f"tool latency p50/p95: {summary.get('tool_latency_p50_ms')}/{summary.get('tool_latency_p95_ms')} ms · "
          f"wall time: {summary.get('wall_time_s')} s",
          "- Tasks: " + "; ".join(f"{t.task_id} [{t.skill}] {t.status} ({t.stop_reason})" for t in st.tasks), ""]
    return "\n".join(L)
