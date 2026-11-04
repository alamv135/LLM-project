"""The research record: typed working state for one diligence run.

Lec 4 "Memory records": working state (this run) is kept separate from
reusable memory (aliases, past cases — see memory.py). Every claim keeps its
source locations, and every piece of evidence keeps its source, identifier,
retrieval time and excerpt (proposal §5.1: traceability).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

CATEGORIES = ["regulatory", "clinical", "technical", "product", "competitive", "commercial"]

# Proposal §4.1 "Synthesis and review": evidence labels, not fixed truth labels.
VERDICTS = [
    "publicly_supported",      # independent public evidence supports the claim as stated
    "partially_supported",     # evidence supports a narrower / differently-worded claim
    "possibly_contradicted",   # independent evidence conflicts with the claim
    "company_reported_only",   # only company materials state it; no independent record found
    "unresolved",              # could not be checked within limits / sources unavailable
]

THEMES = {
    "product_evidence": ["regulatory", "clinical", "technical", "product"],
    "competitive_commercial": ["competitive", "commercial"],
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class SourceChunk:
    chunk_id: str          # e.g. "D2#3"
    doc: str               # file name
    location: str          # human-readable: "section 'Clinical', lines 12-18"
    text: str
    doc_date: str = ""     # date found in document metadata, if any


@dataclass
class Claim:
    claim_id: str
    text: str
    category: str
    source_chunk_ids: list[str]
    product: str = ""
    status_language: str = ""   # e.g. "FDA approved", "completed enrollment"
    materiality: str = "medium"  # high | medium | low


@dataclass
class ResearchTask:
    task_id: str
    claim_ids: list[str]
    question: str
    skill: str
    priority: int = 2           # 1 = highest
    status: str = "pending"     # pending | done | unresolved | failed
    stop_reason: str = ""


@dataclass
class Evidence:
    evidence_id: str
    tool: str
    source: str                 # e.g. "openFDA 510(k)", "ClinicalTrials.gov", "Company material"
    identifier: str             # K-number, NCT ID, PMID, chunk id ...
    url: str
    retrieved_at: str
    fields: dict[str, Any]
    independent: bool           # False for company materials
    synthetic: bool = False     # True when served by the offline synthetic mirror
    query: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    claim_id: str
    verdict: str
    evidence_ids: list[str]
    rationale: str
    conflicts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    identity_confirmed: bool | None = None
    identity_checked: bool = False   # the assessment addressed entity identity (company/applicant match)
    task_id: str = ""
    verified: bool = False      # passed the runtime grounding check


@dataclass
class Counters:
    model_calls: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    format_repairs: int = 0
    verify_rejections: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass
class RunState:
    run_id: str
    company: str
    materials_dir: str
    product: str = ""
    focus: list[str] = field(default_factory=list)    # restrict to themes/categories
    phase: str = "intake"
    started_at: str = field(default_factory=now_iso)
    chunks: list[SourceChunk] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    tasks: list[ResearchTask] = field(default_factory=list)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    findings: dict[str, Finding] = field(default_factory=dict)
    cross_claim_conflicts: list[dict[str, Any]] = field(default_factory=list)
    review_questions: list[dict[str, Any]] = field(default_factory=list)
    human_answers: list[dict[str, Any]] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    counters: Counters = field(default_factory=Counters)
    stop_reason: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def new(company: str, materials_dir: str, product: str = "", focus: list[str] | None = None) -> "RunState":
        rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        return RunState(run_id=rid, company=company, materials_dir=materials_dir,
                        product=product, focus=focus or [])

    # ---- lookups -------------------------------------------------------
    def claim(self, claim_id: str) -> Claim | None:
        return next((c for c in self.claims if c.claim_id == claim_id), None)

    def chunk(self, chunk_id: str) -> SourceChunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)

    def next_evidence_id(self) -> str:
        return f"E{len(self.evidence) + 1}"

    # ---- (de)serialisation for checkpoints -----------------------------
    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "RunState":
        d = json.loads(text)
        st = RunState(run_id=d["run_id"], company=d["company"], materials_dir=d["materials_dir"])
        for k, v in d.items():
            setattr(st, k, v)
        st.chunks = [SourceChunk(**c) for c in d.get("chunks", [])]
        st.claims = [Claim(**c) for c in d.get("claims", [])]
        st.tasks = [ResearchTask(**t) for t in d.get("tasks", [])]
        st.evidence = {k: Evidence(**v) for k, v in d.get("evidence", {}).items()}
        st.findings = {k: Finding(**v) for k, v in d.get("findings", {}).items()}
        st.counters = Counters(**d.get("counters", {}))
        return st
