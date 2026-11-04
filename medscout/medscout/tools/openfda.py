"""openFDA device endpoints: 510(k), PMA, product classification.

Live API: https://open.fda.gov/apis/device/  (no key needed for low volume;
set OPENFDA_API_KEY for higher limits). openFDA returns HTTP 404 for
"No matches found" — that is a valid empty result, not an error.
"""

from __future__ import annotations

import os
from typing import Any

from . import mirror
from .base import ToolContext, ToolError, envelope, string_arg
from .http import build_url, get_json

BASE = "https://api.fda.gov/device"


def _date(d: str | None) -> str:
    d = (d or "").strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


def _search_expr(criteria: dict[str, str]) -> str:
    return " AND ".join(f'{k}:"{v.replace(chr(34), "")}"' for k, v in criteria.items())


def _query(ctx: ToolContext, endpoint: str, mirror_name: str, criteria: dict[str, str],
           limit: int, sort: str, normalise) -> dict[str, Any]:
    if not criteria:
        raise ToolError("permanent", "provide at least one search field")
    if ctx.backend == "live":
        params = {"search": _search_expr(criteria), "limit": limit, "sort": sort,
                  "api_key": os.environ.get("OPENFDA_API_KEY")}
        data = get_json(build_url(f"{BASE}/{endpoint}.json", params), ctx.timeout_s, ctx.cache, not_found_ok=True)
        raw = data.get("results", [])
        total = int(data.get("meta", {}).get("results", {}).get("total", len(raw))) if raw else 0
        synthetic = False
    else:
        recs, synthetic = mirror.load(ctx.mirror_dir, f"openfda_{endpoint}")
        raw = [r for r in recs if all(mirror.field_match(str(r.get(k, "")), v) for k, v in criteria.items())]
        raw.sort(key=lambda r: r.get(sort.split(":")[0], ""), reverse=True)
        total = len(raw)
        raw = raw[:limit]
    out = {"source": f"openFDA {endpoint}", "total": total, "records": [normalise(r) for r in raw]}
    if synthetic:
        out["synthetic"] = True
    if total == 0:
        out["note"] = "no matching records; consider name variants or a different field"
    return out


# ------------------------------------------------------------------ 510(k)
def _norm_510k(r: dict[str, Any]) -> dict[str, Any]:
    ofda = r.get("openfda", {}) or {}
    return {
        "k_number": r.get("k_number", ""),
        "applicant": r.get("applicant", ""),
        "device_name": r.get("device_name", ""),
        "decision_date": _date(r.get("decision_date")),
        "decision_description": r.get("decision_description", ""),
        "clearance_type": r.get("clearance_type", ""),
        "product_code": r.get("product_code", ""),
        "device_class": str(ofda.get("device_class", "")),
        "classification_name": str(ofda.get("device_name", "")),
    }


def fda_510k_search(ctx: ToolContext, applicant: str = "", device_name: str = "", k_number: str = "",
                    product_code: str = "", limit: int = 5) -> dict[str, Any]:
    crit = {k: v for k, v in {"applicant": applicant, "device_name": device_name,
                              "k_number": k_number, "product_code": product_code}.items() if v}
    return _query(ctx, "510k", "510k", crit, limit, "decision_date:desc", _norm_510k)


FDA_510K_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "applicant": string_arg("Company / applicant name as it may appear on FDA records"),
        "device_name": string_arg("Device or trade name words"),
        "k_number": string_arg("510(k) number", pattern="^K[0-9]{6}$"),
        "product_code": string_arg("FDA three-letter product code", pattern="^[A-Z]{3}$"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "anyOf": [{"required": ["applicant"]}, {"required": ["device_name"]},
              {"required": ["k_number"]}, {"required": ["product_code"]}],
}
FDA_510K_OUTPUT = envelope({"type": "object", "required": ["k_number", "applicant", "decision_date"]})


# ------------------------------------------------------------------ PMA
def _norm_pma(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "pma_number": r.get("pma_number", ""),
        "supplement_number": r.get("supplement_number", ""),
        "applicant": r.get("applicant", ""),
        "trade_name": r.get("trade_name", ""),
        "generic_name": r.get("generic_name", ""),
        "decision_date": _date(r.get("decision_date")),
        "decision_code": r.get("decision_code", ""),
        "product_code": r.get("product_code", ""),
    }


def fda_pma_search(ctx: ToolContext, applicant: str = "", trade_name: str = "", pma_number: str = "",
                   product_code: str = "", limit: int = 5) -> dict[str, Any]:
    crit = {k: v for k, v in {"applicant": applicant, "trade_name": trade_name,
                              "pma_number": pma_number, "product_code": product_code}.items() if v}
    return _query(ctx, "pma", "pma", crit, limit, "decision_date:desc", _norm_pma)


FDA_PMA_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "applicant": string_arg("Company / applicant name"),
        "trade_name": string_arg("Device trade name"),
        "pma_number": string_arg("PMA number", pattern="^P[0-9]{6}$"),
        "product_code": string_arg("FDA three-letter product code", pattern="^[A-Z]{3}$"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "anyOf": [{"required": ["applicant"]}, {"required": ["trade_name"]},
              {"required": ["pma_number"]}, {"required": ["product_code"]}],
}
FDA_PMA_OUTPUT = envelope({"type": "object", "required": ["pma_number", "applicant"]})


# ------------------------------------------------------------------ classification
def _norm_class(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_code": r.get("product_code", ""),
        "device_name": r.get("device_name", ""),
        "device_class": str(r.get("device_class", "")),
        "regulation_number": r.get("regulation_number", ""),
        "medical_specialty": r.get("medical_specialty_description", ""),
        "definition": (r.get("definition", "") or "")[:300],
    }


def fda_classification_lookup(ctx: ToolContext, product_code: str = "", device_name: str = "",
                              limit: int = 3) -> dict[str, Any]:
    crit = {k: v for k, v in {"product_code": product_code, "device_name": device_name}.items() if v}
    return _query(ctx, "classification", "classification", crit, limit, "product_code:asc", _norm_class)


FDA_CLASS_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "product_code": string_arg("FDA three-letter product code", pattern="^[A-Z]{3}$"),
        "device_name": string_arg("Generic device type words"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "anyOf": [{"required": ["product_code"]}, {"required": ["device_name"]}],
}
FDA_CLASS_OUTPUT = envelope({"type": "object", "required": ["product_code", "device_class"]})
