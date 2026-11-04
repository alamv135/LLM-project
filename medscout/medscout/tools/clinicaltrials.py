"""ClinicalTrials.gov API v2. Docs: https://clinicaltrials.gov/data-api/api"""

from __future__ import annotations

from typing import Any

from . import mirror
from .base import ToolContext, envelope, string_arg
from .http import build_url, get_json

BASE = "https://clinicaltrials.gov/api/v2/studies"


def _norm(study: dict[str, Any]) -> dict[str, Any]:
    ps = study.get("protocolSection", {}) or {}
    ident = ps.get("identificationModule", {}) or {}
    status = ps.get("statusModule", {}) or {}
    design = ps.get("designModule", {}) or {}
    spons = ps.get("sponsorCollaboratorsModule", {}) or {}
    arms = ps.get("armsInterventionsModule", {}) or {}
    conds = ps.get("conditionsModule", {}) or {}
    enroll = design.get("enrollmentInfo", {}) or {}
    return {
        "nct_id": ident.get("nctId", ""),
        "title": ident.get("briefTitle", ""),
        "sponsor": (spons.get("leadSponsor", {}) or {}).get("name", ""),
        "overall_status": status.get("overallStatus", ""),
        "start_date": (status.get("startDateStruct", {}) or {}).get("date", ""),
        "primary_completion_date": (status.get("primaryCompletionDateStruct", {}) or {}).get("date", ""),
        "primary_completion_type": (status.get("primaryCompletionDateStruct", {}) or {}).get("type", ""),
        "last_update": (status.get("lastUpdatePostDateStruct", {}) or {}).get("date", ""),
        "study_type": design.get("studyType", ""),
        "phases": design.get("phases", []),
        "enrollment": enroll.get("count"),
        "enrollment_type": enroll.get("type", ""),
        "interventions": [i.get("name", "") for i in arms.get("interventions", []) or []][:4],
        "conditions": (conds.get("conditions", []) or [])[:4],
        "has_results": bool(study.get("hasResults", False)),
    }


def clinicaltrials_search(ctx: ToolContext, query: str = "", sponsor: str = "", intervention: str = "",
                          condition: str = "", nct_id: str = "", limit: int = 5) -> dict[str, Any]:
    if not any([query, sponsor, intervention, condition, nct_id]):
        from .base import ToolError
        raise ToolError("permanent", "provide query, sponsor, intervention, condition or nct_id")
    synthetic = False
    if ctx.backend == "live":
        if nct_id:
            data = get_json(f"{BASE}/{nct_id}", ctx.timeout_s, ctx.cache, not_found_ok=True)
            studies = [data] if data else []
            total = len(studies)
        else:
            params = {"query.term": query, "query.spons": sponsor, "query.intr": intervention,
                      "query.cond": condition, "pageSize": limit, "countTotal": "true", "format": "json"}
            data = get_json(build_url(BASE, params), ctx.timeout_s, ctx.cache)
            studies = data.get("studies", [])
            total = int(data.get("totalCount", len(studies)))
        recs = [_norm(s) for s in studies][:limit]
    else:
        raw, synthetic = mirror.load(ctx.mirror_dir, "clinicaltrials")
        recs = []
        for s in raw:
            n = _norm(s)
            text = " ".join(str(v) for v in n.values())
            ok = True
            if nct_id:
                ok &= n["nct_id"] == nct_id
            if sponsor:
                ok &= mirror.field_match(n["sponsor"], sponsor)
            if intervention:
                ok &= mirror.text_score(" ".join(n["interventions"]) + " " + n["title"], intervention) >= 0.5
            if condition:
                ok &= mirror.text_score(" ".join(n["conditions"]), condition) >= 0.5
            if query:
                ok &= mirror.text_score(text, query) >= 0.5
            if ok:
                recs.append(n)
        total = len(recs)
        recs = recs[:limit]
    out: dict[str, Any] = {"source": "ClinicalTrials.gov", "total": total, "records": recs}
    if synthetic:
        out["synthetic"] = True
    if total == 0:
        out["note"] = "no matching studies; try sponsor variants, the product name as intervention, or an NCT id"
    return out


CT_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": string_arg("Free-text search terms"),
        "sponsor": string_arg("Lead sponsor / company name"),
        "intervention": string_arg("Device or intervention name"),
        "condition": string_arg("Condition, e.g. atrial fibrillation"),
        "nct_id": string_arg("Registry id", pattern="^NCT[0-9]{8}$"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "anyOf": [{"required": ["query"]}, {"required": ["sponsor"]}, {"required": ["intervention"]},
              {"required": ["condition"]}, {"required": ["nct_id"]}],
}
CT_OUTPUT = envelope({"type": "object", "required": ["nct_id", "overall_status"]})
