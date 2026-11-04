"""Evaluation harness (proposal §5, Table 1; Lec 4 "Testing strategy").

Runs every scenario in eval/cases.json through each system (medscout | direct | rag)
and scores against hand-set reference outcomes:

  claim_recall          expected claims that the system surfaced
  verdict_accuracy      surfaced claims with an acceptable verdict
  false_resolution      substantive verdicts given where only company/unresolved is acceptable (lower = better)
  evidence_support      claims citing the required public identifier (K-number, NCT id, PMID)
  conflict_recall       expected conflicts that were flagged
  false_conflict        conflicts flagged on compatible claims (lower = better)
  efficiency            model calls, tool calls, tool errors, tokens, cost, wall time

Results: eval/results/<timestamp>.json and .md. With --provider mock the
numbers only test the plumbing (the mock's rules were written alongside the
case); report numbers from a real model.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .baselines import run_baseline
from .config import ROOT
from .state import RunState
from .workflow import Workflow

SUBSTANTIVE = {"publicly_supported", "partially_supported", "possibly_contradicted"}


@contextmanager
def _env(**kv: str):
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: v for k, v in kv.items() if v is not None})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 3) if xs else None


def score(findings: list[dict[str, Any]], evidence: dict[str, dict[str, Any]], expected: list[dict[str, Any]]
          ) -> dict[str, Any]:
    pairs = []
    for e in expected:
        f = next((f for f in findings if all(m.lower() in f["claim_text"].lower() for m in e["match"])), None)
        pairs.append((e, f))
    found = [(e, f) for e, f in pairs if f]
    insufficient = [(e, f) for e, f in found if set(e["verdicts"]) <= {"company_reported_only", "unresolved"}]
    with_ids = [(e, f) for e, f in found if e.get("identifiers")]

    def cites(f, ident):
        return any(evidence.get(x, {}).get("identifier") == ident for x in f.get("evidence_ids", []))

    return {
        "claim_recall": _mean([1.0 if f else 0.0 for _, f in pairs]),
        "verdict_accuracy": _mean([1.0 if f["verdict"] in e["verdicts"] else 0.0 for e, f in found]),
        "false_resolution": _mean([1.0 if f["verdict"] in SUBSTANTIVE else 0.0 for e, f in insufficient]),
        "evidence_support": _mean([1.0 if all(cites(f, i) for i in e["identifiers"]) else 0.0 for e, f in with_ids]),
        "conflict_recall": _mean([1.0 if f.get("conflicts") else 0.0 for e, f in found if e.get("conflict")]),
        "false_conflict": _mean([1.0 if f.get("conflicts") else 0.0 for e, f in found if not e.get("conflict")]),
        "per_claim": [{"match": " & ".join(e["match"]), "expected": e["verdicts"],
                       "got": f["verdict"] if f else None, "ok": bool(f and f["verdict"] in e["verdicts"])}
                      for e, f in pairs],
    }


def _medscout(settings, sc: dict[str, Any], company: str, product: str, cdir: Path) -> dict[str, Any]:
    st = RunState.new(company, str(cdir / "materials"), product, sc.get("focus", []))
    wf = Workflow(settings, st, case_dir=cdir, echo=False)
    t0 = time.perf_counter()
    wf.run()
    findings = [{"claim_text": c.text, "category": c.category, "verdict": f.verdict, "evidence_ids": f.evidence_ids,
                 "conflicts": f.conflicts} for c in st.claims if (f := st.findings.get(c.claim_id))
                and f.task_id != "out_of_scope"]
    return {"system": "medscout", "run_id": st.run_id, "findings": findings,
            "evidence": {k: {"identifier": e.identifier, "independent": e.independent} for k, e in st.evidence.items()},
            "counters": vars(st.counters), "summary": {**wf.tracer.summary(), "wall_time_s": round(time.perf_counter() - t0, 2)},
            "scope_ok": all(c.category in (sc.get("focus") or [c.category]) for c in st.claims
                            if st.findings.get(c.claim_id) and st.findings[c.claim_id].task_id != "out_of_scope")}


def run_eval(settings, systems: list[str], scenarios: list[str] | None = None, on_row=None) -> dict[str, Any]:
    cases = json.loads((ROOT / "eval" / "cases.json").read_text())["scenarios"]
    if scenarios:
        cases = [c for c in cases if c["id"] in scenarios]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "eval" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    settings.runs_dir = settings.runs_dir / f"eval-{stamp}"
    settings.db_path = settings.runs_dir / "eval.db"          # isolated memory per evaluation run
    rows = []
    for sc in cases:
        cdir = ROOT / "data" / "cases" / sc["case"]
        meta = json.loads((cdir / "case.json").read_text())
        for system in systems:
            if system != "medscout" and sc.get("focus"):
                continue   # baselines have no planner; the changed-request scenario tests MedScout only
            with _env(MEDSCOUT_FAULTS=sc.get("faults", ""), MEDSCOUT_MOCK_FAULTS=sc.get("mock_faults", "")):
                t0 = time.perf_counter()
                if system == "medscout":
                    r = _medscout(settings, sc, meta["company"], meta.get("product", ""), cdir)
                else:
                    r = run_baseline(settings, system, meta["company"], meta.get("product", ""), cdir / "materials",
                                     cdir, settings.runs_dir)
                    r["summary"]["wall_time_s"] = round(time.perf_counter() - t0, 2)
            m = score(r["findings"], r["evidence"], sc["expected"])
            c = r["counters"]
            rows.append({"scenario": sc["id"], "kind": sc.get("kind", ""), "system": system, **m,
                         "model_calls": c["model_calls"], "tool_calls": c["tool_calls"],
                         "tool_error_rate": round(c["tool_errors"] / c["tool_calls"], 3) if c["tool_calls"] else 0.0,
                         "format_repairs": c["format_repairs"], "verify_rejections": c["verify_rejections"],
                         "tokens_in": c["tokens_in"], "tokens_out": c["tokens_out"], "cost_usd": c["cost_usd"],
                         "wall_time_s": r["summary"].get("wall_time_s"), "run_id": r.get("run_id", ""),
                         "scope_ok": r.get("scope_ok")})
            if on_row:
                on_row(rows[-1])
            print(f"[eval] {sc['id']:<40} {system:<9} recall={m['claim_recall']} acc={m['verdict_accuracy']} "
                  f"false_res={m['false_resolution']} ev={m['evidence_support']} conflicts={m['conflict_recall']}")
    meta = {"timestamp": stamp, "provider": settings.provider, "model": settings.default_model(),
            "backend": settings.backend, "temperature": settings.temperature, "limits": vars(settings.limits)}
    result = {"meta": meta, "rows": rows}
    (out_dir / f"{stamp}.json").write_text(json.dumps(result, indent=2))
    (out_dir / f"{stamp}.md").write_text(_markdown(result))
    print(f"[eval] wrote {out_dir / (stamp + '.md')}")
    if settings.provider == "mock":
        print("[eval] NOTE: provider=mock — these numbers test the pipeline, not model quality.")
    return result


def _markdown(result: dict[str, Any]) -> str:
    m = result["meta"]
    cols = ["system", "claim_recall", "verdict_accuracy", "false_resolution", "evidence_support", "conflict_recall",
            "false_conflict", "model_calls", "tool_calls", "tool_error_rate", "format_repairs", "verify_rejections",
            "tokens_in", "cost_usd", "wall_time_s"]
    L = [f"# MedScout evaluation {m['timestamp']}", "",
         f"provider `{m['provider']}` · model `{m['model']}` · backend `{m['backend']}` · temperature {m['temperature']}", ""]
    if m["provider"] == "mock":
        L += ["> MOCK provider: results validate the pipeline only; they are not evidence of model quality.", ""]
    for sid in dict.fromkeys(r["scenario"] for r in result["rows"]):
        rows = [r for r in result["rows"] if r["scenario"] == sid]
        L += [f"## {sid}", f"_{rows[0]['kind']}_", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for r in rows:
            L.append("| " + " | ".join("–" if r.get(c) is None else str(r.get(c)) for c in cols) + " |")
        L += ["", "Per-claim (MedScout):", ""]
        ms = next((r for r in rows if r["system"] == "medscout"), rows[0])
        L += [f"- {'✓' if p['ok'] else '✗'} {p['match']}: expected {p['expected']}, got {p['got']}" for p in ms["per_claim"]]
        L.append("")
    return "\n".join(L)
