"""MOCK model: a deterministic, rule-based stand-in for the LLM (provider=mock).

Purpose: run the full agent loop offline (tests, CI, demo without API keys).
It only reads the assembled messages — the same context a real model sees —
and emits the same JSON protocol. Its "reasoning" is a handful of keyword
rules, so results produced with it say nothing about model quality.
Label any mock-based result as such in the report.

Fault injection for reliability tests: MEDSCOUT_MOCK_FAULTS="malformed:1,ungrounded:1"
  malformed  – the next research decision is invalid JSON (exercises format repair)
  ungrounded – the next finish cites a non-existent evidence id (exercises verification)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_FAULTS: dict[str, int] = {}
_FAULTS_LOADED_FROM: str | None = None


def _take_fault(kind: str) -> bool:
    global _FAULTS_LOADED_FROM
    spec = os.environ.get("MEDSCOUT_MOCK_FAULTS", "")
    if spec != _FAULTS_LOADED_FROM:
        _FAULTS.clear()
        for part in filter(None, spec.split(",")):
            k, _, n = part.partition(":")
            _FAULTS[k.strip()] = int(n or 1)
        _FAULTS_LOADED_FROM = spec
    if _FAULTS.get(kind, 0) > 0:
        _FAULTS[kind] -= 1
        return True
    return False


# ----------------------------------------------------------------- text helpers
_LEGAL = {"inc", "llc", "ltd", "corp", "co", "gmbh", "plc", "limited", "corporation"}
_W = re.compile(r"[a-z0-9]+")


def _words(s: str) -> list[str]:
    return [w for w in _W.findall((s or "").lower()) if w not in _LEGAL]


def _block(text: str, start: str, end: str) -> str:
    i = text.find(start)
    j = text.find(end, i)
    if i == -1 or j == -1:
        return ""
    return text[i + len(start): j].strip()


def _field(text: str, name: str) -> str:
    m = re.search(rf"^{name}:\s*(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


def _sentences(text: str) -> list[str]:
    lines = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or ln.startswith("|") or ln.lower().startswith(("date:", "document:")):
            continue
        lines.append(re.sub(r"^[-*•]\s+|^\d+\.\s+|\*\*", "", ln))
    joined = " ".join(lines)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", joined)
    merged: list[str] = []
    for p in parts:   # re-join splits after abbreviations such as "U.S." or "Inc."
        if merged and re.search(r"\b(?:U\.S|Inc|Ltd|Dr|Mr|Ms|vs|e\.g|i\.e|Co)\.$", merged[-1]):
            merged[-1] += " " + p
        else:
            merged.append(p)
    return [s.strip() for s in merged if len(s.strip()) >= 30]


CATEGORY_RULES = [
    ("regulatory", re.compile(r"\bFDA\b|510\(k\)|\bclearance\b|\bcleared\b|\bapprov|\bDe Novo\b|\bCE mark", re.I)),
    ("clinical", re.compile(r"\btrial\b|\bstudy\b|NCT\d{8}|\benrol|\bpeer-reviewed\b|\bpublished\b", re.I)),
    ("competitive", re.compile(r"\bcompetitor|\bcompete", re.I)),
    ("commercial", re.compile(r"\breimburs|\bMedicare\b|\bCMS\b|\binsurers?\b|\bcovered by\b|\brevenue\b|\bcustomers?\b|\bhospitals\b|\bcontracts?\b", re.I)),
    ("technical", re.compile(r"\bsensitivity\b|\bspecificity\b|\baccuracy\b|\bAUC\b", re.I)),
]
STATUS = re.compile(r"FDA[- ]approved|FDA approval|510\(k\) clearance|FDA[- ]cleared|completed enrollment|"
                    r"submission (?:is )?planned|planned|peer-reviewed|recruiting|reimbursed", re.I)
PLANNED = re.compile(r"\bplan(?:ned|s)?\b|\bexpect|\bwill submit|\banticipat|\bupcoming\b", re.I)


def _classify(sentence: str) -> str:
    for cat, rx in CATEGORY_RULES:
        if rx.search(sentence):
            return cat
    return ""


def _claims_from_chunks(chunks: list[dict[str, Any]], product: str) -> list[dict[str, Any]]:
    out = []
    for ch in chunks:
        for s in _sentences(ch["text"]):
            cat = _classify(s)
            if not cat:
                continue
            st = STATUS.search(s)
            out.append({
                "text": s, "category": cat, "source_chunk_ids": [ch["chunk_id"]],
                "product": product if product and product.lower() in s.lower() else product,
                "status_language": st.group(0) if st else "",
                "materiality": "high" if cat in ("regulatory", "clinical") else "medium",
            })
    return out[:25]


# ----------------------------------------------------------------- phases
def respond(messages: list[dict[str, str]]) -> str:
    first_user = messages[1]["content"] if len(messages) > 1 else ""
    phase = _field(first_user, "PHASE")
    if phase == "extract_claims":
        chunks = json.loads(_block(first_user, "CHUNKS_JSON:", "END_CHUNKS") or "[]")
        return json.dumps({"claims": _claims_from_chunks(chunks, _field(first_user, "PRODUCT"))})
    if phase == "plan":
        return _plan(first_user)
    if phase == "research":
        return _research(messages)
    if phase in ("baseline_direct", "baseline_rag"):
        return _baseline(first_user, phase)
    return json.dumps({"error": f"mock policy has no rule for phase {phase!r}"})


SKILL_FOR = {"regulatory": "regulatory-check", "clinical": "clinical-evidence", "technical": "clinical-evidence",
             "competitive": "competitive-context", "commercial": "commercial-signals", "product": "commercial-signals"}
QUESTION_FOR = {
    "regulatory": "What does the public FDA record show for this regulatory claim, and does the pathway match its wording?",
    "clinical": "Does the trial registry / literature record match the clinical claim (status, enrollment, publication)?",
    "technical": "Is the performance figure supported by independent evidence and consistent across company materials?",
    "competitive": "Which comparable devices share the product's FDA product code, and are the named competitors among them?",
    "commercial": "Can public sources establish this commercial claim, and what remains to verify?",
    "product": "Can public sources establish this product claim, and what remains to verify?",
}


def _plan(text: str) -> str:
    claims = json.loads(_block(text, "CLAIMS_JSON:", "END_CLAIMS") or "[]")
    prio = {"high": 1, "medium": 2, "low": 3}
    tasks = [{"claim_ids": [c["claim_id"]], "question": QUESTION_FOR.get(c["category"], QUESTION_FOR["product"]),
              "skill": SKILL_FOR.get(c["category"], "commercial-signals"),
              "priority": prio.get(c.get("materiality", "medium"), 2)} for c in claims]
    return json.dumps({"tasks": tasks})


# ----------------------------------------------------------------- research loop
class _View:
    """What the mock can see of the task: brief + observations + human replies."""

    def __init__(self, messages: list[dict[str, str]]):
        self.task = json.loads(_block(messages[1]["content"], "TASK_JSON:", "END_TASK") or "{}")
        self.obs: list[dict[str, Any]] = []
        self.asked = False
        self.human: str | None = None
        for m in messages[2:]:
            if m["role"] == "assistant" and '"ask_user"' in m["content"]:
                self.asked = True
            if m["role"] == "user":
                for blk in re.findall(r"<application_observation>[^\n]*\n(.*?)\n</application_observation>",
                                      m["content"], re.S):
                    try:
                        self.obs.extend(json.loads(blk))
                    except json.JSONDecodeError:
                        pass
                if "HUMAN_ANSWER:" in m["content"]:
                    self.human = m["content"].split("HUMAN_ANSWER:", 1)[1].split("\n", 1)[0].strip()
                elif "NO_HUMAN_AVAILABLE" in m["content"]:
                    self.human = ""
        self.last = messages[-1]["content"]
        self.company = self.task.get("company", "")
        self.product = self.task.get("product", "")
        self.aliases = self.task.get("aliases", [])
        self.claim = (self.task.get("claims") or [{}])[0]
        self.cid = self.claim.get("claim_id", "C?")
        self.company_ev = self.task.get("company_evidence_ids", [])

    def done(self, tool: str, args: dict[str, Any]) -> bool:
        return any(o["tool"] == tool and o.get("arguments") == args for o in self.obs)

    def records(self, tool: str) -> list[dict[str, Any]]:
        out, seen = [], set()
        for o in self.obs:
            if o["tool"] == tool and o.get("ok"):
                for r in o.get("records", []):
                    if r.get("evidence_id") not in seen:
                        seen.add(r.get("evidence_id"))
                        out.append(r)
        return out

    def failed(self, tool: str) -> bool:
        return any(o["tool"] == tool and not o.get("ok") for o in self.obs)

    def calls(self, wanted: list[tuple[str, dict[str, Any]]], thought: str) -> dict[str, Any] | None:
        todo = [{"tool": t, "arguments": a} for t, a in wanted if not self.done(t, a)][:3]
        return {"action": "tool_calls", "thought": thought, "calls": todo} if todo else None

    def match(self, name: str) -> str:
        n = " ".join(_words(name))
        if n == " ".join(_words(self.company)) or n in {" ".join(_words(a)) for a in self.aliases}:
            return "exact"
        cw, nw = _words(self.company), set(_words(name))
        return "partial" if cw and all(w in nw for w in cw) else "no"

    def finish(self, verdict: str, ev: list[str], rationale: str, conflicts=None, questions=None,
               identity: Any = "unset", thought="") -> dict[str, Any]:
        a = {"claim_id": self.cid, "verdict": verdict, "evidence_ids": list(dict.fromkeys(ev)),
             "rationale": rationale, "conflicts": conflicts or [], "open_questions": questions or []}
        if identity != "unset":          # only regulatory checks address entity identity
            a["identity_confirmed"] = identity
        return {"action": "finish", "thought": thought or "evidence gathered; assessing", "assessments": [a]}


def _research(messages: list[dict[str, str]]) -> str:
    v = _View(messages)
    if not v.obs and not v.asked and "FORMAT_ERROR" not in v.last and _take_fault("malformed"):
        return 'Sure! I will search FDA first: {"action": "tool_calls", "calls": [{"tool": "fda_510k_search"'
    cat = v.claim.get("category", "product")
    handler = {"regulatory": _regulatory, "clinical": _clinical, "technical": _technical,
               "competitive": _competitive}.get(cat, _commercial)
    d = handler(v)
    if d["action"] == "finish" and "VERIFICATION_FAILED" not in v.last and _take_fault("ungrounded"):
        d["assessments"][0]["evidence_ids"].append("E999")
    return json.dumps(d)


def _identity(v: _View, recs: list[dict[str, Any]]):
    """Split FDA records into ours / probable-ours / other entities; maybe ask the human."""
    exact = [r for r in recs if v.match(r.get("applicant", "")) == "exact"]
    partial = [r for r in recs if v.match(r.get("applicant", "")) == "partial"]
    if partial and v.human is None and not v.asked and not exact:
        p = partial[0]["applicant"]
        return None, {"action": "ask_user", "thought": "applicant name differs from the company name",
                      "question": f"FDA lists the applicant as '{p}'. Is this the same company as '{v.company}'?",
                      "options": ["yes", "no"], "alias_candidate": p}
    if v.human and v.human.lower().startswith("n"):
        partial = []
    identity = True if (exact or (partial and v.human and v.human.lower().startswith("y"))) else None
    return (exact + partial, identity), None


def _regulatory(v: _View) -> dict[str, Any]:
    text = v.claim.get("text", "")
    says_approved = bool(re.search(r"\bapprov", text, re.I)) and not re.search(r"clear|510\(k\)", text, re.I)
    knum = re.search(r"\bK\d{6}\b", text)
    want = [("fda_510k_search", {"applicant": v.company})]
    if says_approved:
        want.append(("fda_pma_search", {"applicant": v.company}))
    if knum:
        want.append(("fda_510k_search", {"k_number": knum.group(0)}))
    d = v.calls(want, "check FDA records for the company (510(k) and, for 'approved', PMA)")
    if d:
        return d
    res, ask = _identity(v, v.records("fda_510k_search"))
    if ask:
        return ask
    matched, identity = res
    if not matched and v.product and not v.done("fda_510k_search", {"device_name": v.product}):
        return v.calls([("fda_510k_search", {"device_name": v.product})],
                       "no record under the company name; retry with the product name")
    if not matched:   # product-name search may have found records under a variant name
        res, ask = _identity(v, v.records("fda_510k_search"))
        if ask:
            return ask
        matched, identity = res
    pma = [r for r in v.records("fda_pma_search") if v.match(r.get("applicant", "")) != "no"]
    idq = [] if identity else ([f"Confirm that FDA applicant '{matched[0]['applicant']}' is the same legal entity "
                                f"as '{v.company}'."] if matched else [])
    if PLANNED.search(text):
        ev = v.company_ev + [r["evidence_id"] for r in matched[:1]]
        note = (f" The existing clearance {matched[0]['k_number']} covers the current device, not the planned one."
                if matched else "")
        return v.finish("company_reported_only", ev,
                        "The claim describes a planned submission; FDA databases only list decided submissions, "
                        f"so no public record can confirm it yet.{note}",
                        questions=["Request the planned submission timeline and any FDA pre-submission feedback."])
    if matched:
        prod = [r for r in matched if v.product and v.product.lower() in r.get("device_name", "").lower()] or matched
        r = prod[0]
        if says_approved and not pma:
            return v.finish(
                "partially_supported", v.company_ev + [r["evidence_id"]],
                f"FDA lists a 510(k) clearance for {r['device_name']} ({r['k_number']}, {r.get('decision_date', '')}) "
                f"[{r['evidence_id']}], but no PMA approval for the company.",
                conflicts=[f"Claim says 'FDA approved'; the public record is a 510(k) clearance ({r['k_number']}, "
                           f"'{r.get('decision_description', '')}'). A 510(k) clearance is not a PMA approval."],
                questions=idq, identity=identity)
        return v.finish("publicly_supported", v.company_ev + [r["evidence_id"]],
                        f"FDA 510(k) record {r['k_number']} for {r['device_name']} ({r.get('decision_date', '')}) "
                        f"[{r['evidence_id']}] matches the claimed regulatory status.", questions=idq, identity=identity)
    if v.failed("fda_510k_search"):
        return v.finish("unresolved", v.company_ev, "The FDA source failed during this run; the claim could not be checked.",
                        questions=["Re-run the FDA check when the source is available."])
    return v.finish("company_reported_only", v.company_ev,
                    "No FDA 510(k) or PMA record was found under the company or product name; only company materials "
                    "state this.", questions=["Request the K-number / PMA number and the exact FDA applicant name."])


_INT = re.compile(r"(\d[\d,]*)\s+(?:patients|participants|subjects)", re.I)


def _clinical(v: _View) -> dict[str, Any]:
    text = v.claim.get("text", "")
    nct = re.search(r"NCT\d{8}", text)
    trial_claim = bool(re.search(r"\btrial\b|\bstudy\b|\benrol|patients", text, re.I))
    pub_claim = bool(re.search(r"publish|peer-reviewed|journal", text, re.I))
    want = []
    if trial_claim:
        want.append(("clinicaltrials_search", {"nct_id": nct.group(0)} if nct else {"sponsor": v.company}))
    if pub_claim:
        want.append(("pubmed_search", {"query": f"{v.product} validation"}))
    d = v.calls(want, "check the trial registry and literature")
    if d:
        return d
    trials = v.records("clinicaltrials_search")
    if trial_claim and not trials and v.product and not v.done("clinicaltrials_search", {"intervention": v.product}):
        return v.calls([("clinicaltrials_search", {"intervention": v.product})],
                       "no registration under the sponsor name; retry with the device as intervention")
    if trial_claim and trials:
        t = next((t for t in trials if nct and t.get("nct_id") == nct.group(0)), None) or \
            next((t for t in trials if v.product.lower() in (t.get("title", "") + " ".join(t.get("interventions", []))).lower()), trials[0])
        conflicts = []
        active = t.get("overall_status") in {"RECRUITING", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION"}
        if re.search(r"complet|finished|concluded", text, re.I) and active:
            conflicts.append(f"Claim says the study completed enrollment; ClinicalTrials.gov {t['nct_id']} status is "
                             f"{t['overall_status']} (last update {t.get('last_update', '?')}).")
        m = _INT.search(text)
        if m and t.get("enrollment") and int(m.group(1).replace(",", "")) != int(t["enrollment"]):
            conflicts.append(f"Claim: {m.group(1)} patients; registry enrollment: {t['enrollment']} "
                             f"({t.get('enrollment_type', '').lower() or 'type unknown'}).")
        if conflicts:
            return v.finish("possibly_contradicted", v.company_ev + [t["evidence_id"]],
                            f"Registry record {t['nct_id']} [{t['evidence_id']}] differs from the claim.",
                            conflicts=conflicts, questions=["Ask the company for the current enrollment and status, "
                                                            "and why the registry has not been updated."])
        return v.finish("publicly_supported", v.company_ev + [t["evidence_id"]],
                        f"Registry record {t['nct_id']} [{t['evidence_id']}] is consistent with the claim.")
    pubs = v.records("pubmed_search")
    if pub_claim and pubs:
        p = pubs[0]
        return v.finish("publicly_supported", v.company_ev + [p["evidence_id"]],
                        f"A matching publication exists: PMID {p['pmid']}, {p.get('journal', '')} {p.get('pub_date', '')} "
                        f"[{p['evidence_id']}]. Study design and reported figures were not assessed.",
                        questions=["Have a clinical specialist review the study design and endpoints."])
    if v.failed("clinicaltrials_search") or v.failed("pubmed_search"):
        return v.finish("unresolved", v.company_ev, "A clinical evidence source failed during this run.",
                        questions=["Re-run the registry / literature check when the source is available."])
    return v.finish("company_reported_only", v.company_ev,
                    "No registry entry or publication matching the claim was found; only company materials state it.",
                    questions=["Request the registry id or publication reference."])


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _technical(v: _View) -> dict[str, Any]:
    text = v.claim.get("text", "")
    metric = next((m for m in ("sensitivity", "specificity", "accuracy", "AUC") if m.lower() in text.lower()), "performance")
    d = v.calls([("search_company_docs", {"query": metric}), ("pubmed_search", {"query": f"{v.product} validation"})],
                "compare the figure across company materials and look for independent validation")
    if d:
        return d
    vals: dict[str, tuple[str, str]] = {}
    for r in v.records("search_company_docs"):
        for sent in re.split(r"(?<=[.!?])\s+", r.get("text", "")):
            if metric.lower() in sent.lower():
                for num in _PCT.findall(sent):
                    vals.setdefault(num, (r.get("doc", ""), r["evidence_id"]))
                    break
    conflicts = []
    if len(vals) > 1:
        conflicts.append(f"Company materials report different {metric} values: " +
                         "; ".join(f"{n}% ({doc}, {e})" for n, (doc, e) in vals.items()))
    pubs = v.records("pubmed_search")
    ev = v.company_ev + [e for _, e in vals.values()] + [p["evidence_id"] for p in pubs[:1]]
    pub_note = (f" A related publication exists (PMID {pubs[0]['pmid']}) [{pubs[0]['evidence_id']}], but its "
                "reported figures are not available in the retrieved fields.") if pubs else ""
    return v.finish("company_reported_only", ev,
                    f"The {metric} figure appears only in company materials.{pub_note}", conflicts=conflicts,
                    questions=[f"Obtain the validation report: which {metric} figure is current, on what dataset and "
                               "reference standard?"])


def _competitive(v: _View) -> dict[str, Any]:
    text = v.claim.get("text", "")
    d = v.calls([("fda_510k_search", {"applicant": v.company})], "find our product code first")
    if d:
        return d
    own = [r for r in v.records("fda_510k_search") if v.match(r.get("applicant", "")) != "no"]
    codes = list(dict.fromkeys(r["product_code"] for r in own if r.get("product_code")))
    if codes:
        d = v.calls([("fda_510k_search", {"product_code": codes[0], "limit": 10}),
                     ("fda_classification_lookup", {"product_code": codes[0]})],
                    f"list devices sharing product code {codes[0]}")
        if d:
            return d
    m = re.search(r"(?:competitors? (?:are|include)|compete[s]? (?:with|against))\s+(.*)", text, re.I)
    named = [n.strip(" .") for n in re.split(r",|\band\b", m.group(1))] if m else []
    named = [n for n in named if n]
    same = [r for r in v.records("fda_510k_search") if codes and r.get("product_code") == codes[0]
            and v.match(r.get("applicant", "")) == "no"]
    found = [n for n in named if any(set(_words(n)) <= set(_words(r["applicant"])) for r in same)]
    missing = [n for n in named if n not in found]
    unnamed = [r for r in same if not any(set(_words(n)) <= set(_words(r["applicant"])) for n in named)]
    cls = v.records("fda_classification_lookup")
    ev = v.company_ev + [r["evidence_id"] for r in same if any(set(_words(n)) <= set(_words(r["applicant"])) for n in found)]
    ev += [r["evidence_id"] for r in unnamed[:3]] + [c["evidence_id"] for c in cls[:1]]
    qs = []
    if missing:
        qs.append(f"No FDA record under product code {codes[0] if codes else '?'} for: {', '.join(missing)}. "
                  "Are they US-market competitors, and under which product code?")
    if unnamed:
        qs.append("Same product-code device holders not named by the company: " +
                  ", ".join(f"{r['applicant']} ({r['k_number']})" for r in unnamed[:3]) + ". Are they competitors?")
    if not same:
        return v.finish("company_reported_only" if not v.failed("fda_510k_search") else "unresolved", v.company_ev,
                        "No comparable devices could be listed from FDA records.",
                        questions=qs or ["Identify comparable products manually."])
    verdict = "publicly_supported" if found and not missing and not unnamed else (
        "partially_supported" if found else "company_reported_only")
    return v.finish(verdict, ev,
                    f"FDA lists {len(same)} other device holder(s) under product code {codes[0]}; "
                    f"named competitors found there: {', '.join(found) or 'none'}. "
                    "Shared product codes indicate comparable devices, not market share.", questions=qs)


def _commercial(v: _View) -> dict[str, Any]:
    text = v.claim.get("text", "")
    kw = " ".join([w for w in _words(text) if len(w) > 5][:3]) or "customers"
    d = v.calls([("search_company_docs", {"query": kw})], "look for supporting detail in company materials")
    if d:
        return d
    docs = v.records("search_company_docs")
    if re.search(r"reimburs|medicare|cms|coverage|covered|insur", text, re.I):
        q = ("Verify coverage and coding with the CMS Medicare Coverage Database / MAC policies (source not integrated "
             "in this MVP). FDA authorisation does not establish coverage.")
    else:
        q = "Request customer contracts or revenue evidence; public sources rarely establish commercial traction."
    return v.finish("company_reported_only", v.company_ev + [r["evidence_id"] for r in docs[:1]],
                    "Only company materials make this commercial claim; the integrated public sources cannot "
                    "establish it.", questions=[q])


# ----------------------------------------------------------------- baselines
def _baseline(text: str, phase: str) -> str:
    chunks = json.loads(_block(text, "CHUNKS_JSON:", "END_CHUNKS") or "[]")
    retrieved = json.loads(_block(text, "RETRIEVED_JSON:", "END_RETRIEVED") or "[]")
    product = _field(text, "PRODUCT")
    out = []
    for c in _claims_from_chunks(chunks, product):
        verdict, ev = "company_reported_only", []
        if phase == "baseline_rag" and c["category"] in ("regulatory", "clinical"):
            hit = next((r for r in retrieved if product and product.lower() in json.dumps(r).lower()), None)
            if hit:   # naive synthesis: any retrieved record mentioning the product counts as support
                verdict, ev = "publicly_supported", [hit["evidence_id"]]
        out.append({"claim_text": c["text"], "category": c["category"], "verdict": verdict,
                    "evidence_ids": ev, "rationale": "mock baseline", "conflicts": []})
    return json.dumps({"findings": out})
