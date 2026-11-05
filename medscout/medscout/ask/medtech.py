"""MedScout's public-evidence tools inside the Ask chat.

The chat model gets the same five read-only tools the research agent uses (openFDA 510(k), PMA,
classification, ClinicalTrials.gov, PubMed). Calls go through MedScout's ToolGateway, so the
input-schema check, timeout, bounded retry, output contract and HTTP cache all apply. Each
returned record becomes a numbered source with a link, which the answer cites as [n].

Backend: "live" calls the real public APIs; a demo case name ("nordleaf", "quillon") reads that
case's offline, synthetic mirror (useful without internet, and clearly marked as synthetic).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import ROOT, Settings as MedScoutSettings
from ..memory import Memory
from ..state import RunState
from ..tools import ToolContext, ToolGateway, build_specs
from ..tools.registry import PUBLIC_SOURCE_TOOLS
from ..trace import Tracer

MAX_RECORDS = 6

# Ollama passes only simple JSON schemas to the model (no anyOf / pattern), so the rules that the
# full input schema enforces are also written into the description.
HINTS = {
    "fda_510k_search": "Give at least one of applicant, device_name, k_number (like K123456) or product_code "
                       "(three capital letters).",
    "fda_pma_search": "Give at least one of applicant, trade_name, pma_number (like P123456) or product_code.",
    "fda_classification_lookup": "Give product_code (three capital letters) or device_name.",
    "clinicaltrials_search": "Give at least one of query, sponsor, intervention, condition or nct_id "
                             "(like NCT01234567).",
    "pubmed_search": "query is required.",
}
LABELS = {
    "fda_510k_search": "Searched FDA 510(k) clearances",
    "fda_pma_search": "Searched FDA PMA approvals",
    "fda_classification_lookup": "Looked up the FDA product classification",
    "clinicaltrials_search": "Searched ClinicalTrials.gov",
    "pubmed_search": "Searched PubMed",
}


def demo_cases() -> List[str]:
    return sorted(p.parent.name for p in (ROOT / "data" / "cases").glob("*/public_mirror"))


def record_title(tool: str, r: Dict[str, Any]) -> str:
    """A short human title for one record (used in the source list)."""
    def pick(*keys: str) -> List[str]:
        return [str(r[k]) for k in keys if r.get(k)]
    if tool == "fda_510k_search":
        bits = pick("device_name") + ["(" + ", ".join(pick("k_number", "applicant", "decision_date")) + ")"]
    elif tool == "fda_pma_search":
        bits = pick("trade_name") + ["(" + ", ".join(pick("pma_number", "applicant", "decision_date")) + ")"]
    elif tool == "fda_classification_lookup":
        bits = pick("product_code", "device_name") + (["Class " + str(r["device_class"])] if r.get("device_class") else [])
    elif tool == "clinicaltrials_search":
        bits = pick("title") + ["(" + ", ".join(pick("nct_id", "overall_status")) + ")"]
    else:
        bits = pick("title") + ["(" + ", ".join(pick("journal", "pub_date")) + ")"]
    return " ".join(b for b in bits if b and b != "()").strip() or tool


class MedTechTools:
    def __init__(self, base: MedScoutSettings):
        self.base = base
        self.specs = {name: spec for name, spec in build_specs().items() if name in PUBLIC_SOURCE_TOOLS}

    @property
    def names(self) -> set:
        return set(self.specs)

    def tool_defs(self) -> List[Dict[str, Any]]:
        """The tools in Ollama's function-calling format."""
        out = []
        for name in sorted(self.specs):
            spec = self.specs[name]
            props = {}
            for key, p in spec.input_schema.get("properties", {}).items():
                prop = {"type": p.get("type", "string")}
                desc = p.get("description", "")
                if p.get("maximum"):
                    desc = (desc + " " if desc else "") + "(1 to %d)" % p["maximum"]
                if desc:
                    prop["description"] = desc
                props[key] = prop
            out.append({"type": "function", "function": {
                "name": name,
                "description": "%s Source: %s. %s" % (spec.description, spec.source_label, HINTS.get(name, "")),
                "parameters": {"type": "object", "properties": props,
                               "required": list(spec.input_schema.get("required", []))},
            }})
        return out

    def label(self, name: str, args: Dict[str, Any]) -> str:
        shown = ", ".join("%s" % v for k, v in args.items() if k != "limit" and v)
        return '%s for "%s"' % (LABELS.get(name, name), shown) if shown else LABELS.get(name, name)

    def run(self, name: str, args: Dict[str, Any], backend: str,
            add_source: Callable[[str, str, str], int]) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
        """Run one tool. Returns (text for the model, results for the page, error or None)."""
        spec = self.specs[name]
        args = {k: v for k, v in args.items() if v not in (None, "", [])}
        mirror: Optional[Path] = None
        if backend != "live":
            mirror = ROOT / "data" / "cases" / backend / "public_mirror"
            if not mirror.exists():
                return ("Tool error: the demo case %r has no offline data. Use live data in Settings." % backend,
                        [], "No offline data for demo case %r" % backend)
        state = RunState.new("ask", "")
        mem = Memory(self.base.db_path)
        ctx = ToolContext("live" if backend == "live" else "mirror", mirror, evidence=state.evidence, cache=mem,
                          timeout_s=self.base.limits.tool_timeout_s)
        gateway = ToolGateway(self.specs, ctx, state, self.base.limits, Tracer(None, state.run_id), faults="")
        try:
            obs = gateway.run_batch([{"tool": name, "arguments": args}])[0]
        finally:
            gateway.close()
            mem.close()
        if not obs.ok:
            hint = " Check the argument format." if obs.error_kind == "permanent" else ""
            return ("Tool error (%s): %s.%s" % (obs.error_kind, obs.error, hint), [], obs.error)

        data = obs.data or {}
        records = data.get("records", [])[:MAX_RECORDS]
        results = []
        lines = ["%s results for %s (%d found, showing %d):" % (spec.source_label, json.dumps(args),
                                                              data.get("total", len(records)), len(records))]
        if data.get("synthetic"):
            lines.append("NOTE: these records are SYNTHETIC demo data from an offline mirror, not real FDA/registry "
                         "records. Say so in the answer.")
        if data.get("note"):
            lines.append("Note: %s" % data["note"])
        lines.append("")
        for r in records:
            try:
                url = spec.url_template.format(**r) if spec.url_template else ""
            except (KeyError, IndexError, ValueError):
                url = ""
            title = record_title(name, r)
            n = add_source(title, url or ("%s %s" % (spec.source_label, r.get(spec.record_id_field, ""))), "")
            compact = {k: v for k, v in r.items() if v not in ("", None, [], {})}
            lines += ["[%d] %s" % (n, json.dumps(compact, ensure_ascii=False)[:900]), "URL: %s" % (url or "-"), ""]
            results.append({"n": n, "title": title, "url": url, "snippet": ""})
        if not records:
            lines.append("No records matched. Try other spellings, the company's legal name, or a product code.")
        else:
            lines.append("Cite these as [n]. A 510(k) is a clearance, not an approval; only a PMA is an FDA approval.")
        return "\n".join(lines), results, None
