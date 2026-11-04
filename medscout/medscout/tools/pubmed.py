"""PubMed via NCBI E-utilities (esearch + esummary).

Docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/ . NCBI asks clients to
send `tool` and `email`; set NCBI_EMAIL (and optionally NCBI_API_KEY).
"""

from __future__ import annotations

import os
from typing import Any

from . import mirror
from .base import ToolContext, envelope, string_arg
from .http import build_url, get_json

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _norm(uid: str, s: dict[str, Any]) -> dict[str, Any]:
    doi = next((a.get("value", "") for a in s.get("articleids", []) or [] if a.get("idtype") == "doi"), "")
    return {
        "pmid": str(s.get("uid", uid)),
        "title": s.get("title", ""),
        "journal": s.get("fulljournalname") or s.get("source", ""),
        "pub_date": s.get("pubdate", ""),
        "authors": [a.get("name", "") for a in (s.get("authors", []) or [])[:3]],
        "doi": doi,
        "pub_types": (s.get("pubtype", []) or [])[:3],
    }


def pubmed_search(ctx: ToolContext, query: str, limit: int = 5) -> dict[str, Any]:
    synthetic = False
    if ctx.backend == "live":
        common = {"tool": "medscout", "email": os.environ.get("NCBI_EMAIL"), "api_key": os.environ.get("NCBI_API_KEY")}
        es = get_json(build_url(f"{EUTILS}/esearch.fcgi", {"db": "pubmed", "term": query, "retmode": "json",
                                                          "retmax": limit, "sort": "relevance", **common}),
                      ctx.timeout_s, ctx.cache)
        ids = es.get("esearchresult", {}).get("idlist", [])
        total = int(es.get("esearchresult", {}).get("count", len(ids)))
        recs = []
        if ids:
            sm = get_json(build_url(f"{EUTILS}/esummary.fcgi", {"db": "pubmed", "id": ",".join(ids),
                                                               "retmode": "json", **common}),
                          ctx.timeout_s, ctx.cache)
            res = sm.get("result", {})
            recs = [_norm(i, res[i]) for i in res.get("uids", ids) if i in res]
    else:
        raw, synthetic = mirror.load(ctx.mirror_dir, "pubmed")
        scored = []
        for s in raw:
            n = _norm(str(s.get("uid", "")), s)
            text = " ".join([n["title"], n["journal"], " ".join(n["authors"]), s.get("_abstract_keywords", "")])
            sc = mirror.text_score(text, query)
            if sc >= 0.5:
                scored.append((sc, n))
        scored.sort(key=lambda x: -x[0])
        total = len(scored)
        recs = [n for _, n in scored[:limit]]
    out: dict[str, Any] = {"source": "PubMed", "total": total, "records": recs}
    if synthetic:
        out["synthetic"] = True
    if total == 0:
        out["note"] = "no matching articles; try fewer or broader terms"
    return out


PUBMED_INPUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query"],
    "properties": {
        "query": string_arg("PubMed query, e.g. 'wearable ECG patch atrial fibrillation validation'"),
        "limit": {"type": "integer", "minimum": 1, "maximum": 10},
    },
}
PUBMED_OUTPUT = envelope({"type": "object", "required": ["pmid", "title"]})
