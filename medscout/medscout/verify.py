"""Runtime verification of model assessments (grounding + domain guardrails).

The model proposes a verdict; Python checks it is supported by the record
before it enters the brief (proposal C3/C4: cite evidence, keep honest gaps).
Failed checks are returned to the model once for bounded revision
(Lec 4 "Test-time compute: bounded revision"); if they still fail, the
verdict is downgraded to `unresolved` rather than published.
"""

from __future__ import annotations

import re
from typing import Any

from .state import RunState, ResearchTask

SUBSTANTIVE = {"publicly_supported", "partially_supported", "possibly_contradicted"}
_APPROVED = re.compile(r"\bapprov", re.I)
_CLEARED = re.compile(r"\bclear|510\(k\)", re.I)


def verify_assessments(assessments: list[dict[str, Any]], state: RunState, task: ResearchTask,
                       observed_ids: set[str]) -> list[str]:
    problems: list[str] = []
    seen = set()
    for a in assessments:
        cid = a["claim_id"]
        seen.add(cid)
        if cid not in task.claim_ids:
            problems.append(f"{cid}: not a claim of this task (expected {task.claim_ids})")
            continue
        claim = state.claim(cid)
        cited = a.get("evidence_ids", [])
        unknown = [e for e in cited if e not in state.evidence]
        if unknown:
            problems.append(f"{cid}: cites unknown evidence ids {unknown}; cite only ids shown in observations")
        not_seen = [e for e in cited if e in state.evidence and e not in observed_ids]
        if not_seen:
            problems.append(f"{cid}: cites {not_seen} which were not retrieved in this task")
        independent = [e for e in cited if e in state.evidence and state.evidence[e].independent]
        v = a["verdict"]
        if v in SUBSTANTIVE and not independent:
            problems.append(f"{cid}: verdict '{v}' requires at least one independent (non-company) evidence id")
        if v == "possibly_contradicted" and not a.get("conflicts"):
            problems.append(f"{cid}: 'possibly_contradicted' must state the conflict in 'conflicts'")
        if v == "unresolved" and not a.get("open_questions"):
            problems.append(f"{cid}: 'unresolved' must list at least one open question")
        # Domain guardrail: '510(k) cleared' != 'FDA approved' (proposal §3.2)
        if claim and v == "publicly_supported" and _APPROVED.search(claim.text) and not _CLEARED.search(claim.text):
            tools = {state.evidence[e].tool for e in independent}
            if tools and tools <= {"fda_510k_search", "fda_classification_lookup"}:
                problems.append(f"{cid}: claim says 'approved' but cited FDA evidence is a 510(k) clearance; "
                                "a clearance is not a PMA approval — use partially_supported and state the conflict")
    missing = [c for c in task.claim_ids if c not in seen]
    if missing:
        problems.append(f"missing assessments for {missing}")
    return problems
