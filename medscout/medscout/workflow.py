"""MedScout workflow graph (proposal Fig. 1).

    intake -> extract_claims -> plan -> research -> reconcile -> brief -> done
                                          ^  (one bounded ReAct loop per task)

Nodes implement work; `run()` implements the edges. The state is
checkpointed after every node and after every research task, so a run can be
resumed with `python run.py resume <run_id>` — the same contract as a
LangGraph StateGraph compiled with a durable checkpointer.

Which decisions need the model, and which belong in Python (Lec 4 "Demand analysis"):
  model : claim extraction, research planning, tool selection/arguments,
          when to stop / ask, the verdict and its rationale
  python: chunking + source locations, schema/allowlist validation, budgets,
          retries, evidence ids + provenance, grounding checks, cross-document
          numeric consistency, coverage of every claim, brief rendering
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from . import brief as brief_mod
from .context import ContextBuilder
from .harness import ResearchHarness
from .intake import load_materials
from .llm import LLMError, call_model, make_llm, messages_tokens
from .memory import Memory
from .protocol import EXTRACT_SCHEMA, PLAN_SCHEMA, ProtocolError, parse
from .skills import SkillLibrary
from .state import CATEGORIES, THEMES, VERDICTS, Claim, Finding, ResearchTask, RunState  # noqa: F401
from .tools import ToolContext, ToolGateway, build_specs, catalog_text
from .tools.mirror import words

DEFAULT_SKILL = {"regulatory": "regulatory-check", "clinical": "clinical-evidence", "technical": "clinical-evidence",
                 "competitive": "competitive-context", "commercial": "commercial-signals",
                 "product": "commercial-signals"}
NEXT = {"intake": "extract_claims", "extract_claims": "plan", "plan": "research",
        "research": "reconcile", "reconcile": "brief", "brief": "done"}


def render(template: str, **values: str) -> str:
    for k, v in values.items():
        template = template.replace("{{" + k + "}}", v)
    return template


class Workflow:
    def __init__(self, settings, state: RunState, *, case_dir: Path | None = None,
                 ask_fn: Callable[[str, list[str]], str | None] | None = None, echo: bool = True,
                 ctrl=None, faults: str | None = None):
        self.settings, self.state = settings, state
        self.ctrl = ctrl          # optional RunController (web UI)
        self.L = settings.limits
        self.run_dir = settings.runs_dir / state.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.memory = Memory(settings.db_path)
        from .trace import Tracer
        self.tracer = Tracer(self.run_dir / "trace.jsonl", state.run_id, echo)
        self.skills = SkillLibrary(settings.skills_dir)
        self.database = None
        if settings.use_database or state.config.get("use_database"):
            from .localdb import DatabaseError
            from .user_settings import open_database
            try:
                self.database = open_database(settings.runs_dir)
            except DatabaseError as exc:
                self.tracer.event("warning", phase="setup", message=f"database not available: {exc}")
        self.specs = build_specs(include_database=self.database is not None)
        # Uploaded documents and the user's database are private; demo cases are fictional.
        private = bool(state.config.get("private", case_dir is None or self.database is not None))
        mirror = state.config.get("mirror_dir") or (
            str(case_dir / "public_mirror") if case_dir and (case_dir / "public_mirror").exists() else "")
        state.config.update(private=private, use_database=self.database is not None, allow_cloud=settings.allow_cloud)
        state.config.update(provider=settings.provider, model=settings.default_model(), backend=settings.backend,
                            tool_transport=settings.tool_transport, mirror_dir=mirror,
                            limits=vars(self.L))
        self.tool_ctx = ToolContext(backend=settings.backend, mirror_dir=Path(mirror) if mirror else None,
                                    chunks=state.chunks, evidence=state.evidence, skills=self.skills,
                                    cache=self.memory, timeout_s=self.L.tool_timeout_s, database=self.database,
                                    database_notes=_db_notes(settings) if self.database else "")
        mcp_args = ["--backend", settings.backend] + (["--mirror", mirror] if mirror else [])
        self.gateway = ToolGateway(self.specs, self.tool_ctx, state, self.L, self.tracer,
                                   transport=settings.tool_transport, mcp_server_args=mcp_args, faults=faults)
        self.llm = make_llm(settings, private=private)
        if hasattr(self.llm, "preflight"):
            self.llm.preflight()        # e.g. a cloud model chosen for private documents: stop now, not later
        system = render((settings.prompts_dir / "system.md").read_text(),
                        TOOL_CATALOG=catalog_text(self.specs), SKILL_INDEX=self.skills.index_text(),
                        VERDICTS=", ".join(VERDICTS))
        self.ctx = ContextBuilder(system, self.L)
        self.ask_fn = ask_fn
        self.deadline = self._now() + self.L.run_deadline_s

    def _now(self) -> float:
        return self.ctrl.now() if self.ctrl else time.monotonic()

    # ------------------------------------------------------------------ edges
    def run(self) -> RunState:
        from .control import StopRequested
        st = self.state
        st.stop_reason = ""
        self.tracer.event("phase", phase=st.phase, run_id=st.run_id, provider=self.settings.provider,
                          backend=self.settings.backend, transport=self.settings.tool_transport)
        try:
            while st.phase != "done":
                if self.ctrl:
                    self.ctrl.gate("node", phase=st.phase)
                node = getattr(self, st.phase)
                node()
                st.phase = NEXT[st.phase]
                self.checkpoint()
                if st.phase != "done":
                    self.tracer.event("phase", phase=st.phase)
        except StopRequested:
            st.stop_reason = "stopped by reviewer"
            self.checkpoint()          # resume continues from the unfinished phase / task
            self.tracer.event("stop", reason=st.stop_reason, phase=st.phase)
        except KeyboardInterrupt:
            self.checkpoint()
            print(f"\n[medscout] interrupted; resume with: python run.py resume {st.run_id}")
            raise
        finally:
            self.gateway.close()
            self.memory.close()
            if self.database is not None:
                self.database.close()
        return st

    def checkpoint(self) -> None:
        js = self.state.to_json()
        self.memory.save_checkpoint(self.state.run_id, self.state.phase, js)
        (self.run_dir / "state.json").write_text(js)

    def _model_json(self, content: str, schema: dict[str, Any], purpose: str) -> dict[str, Any] | None:
        """One-shot model call with a single bounded format repair."""
        msgs = self.ctx.one_shot(content)
        for attempt in range(1 + self.L.max_format_repairs):
            try:
                res = call_model(self.llm, msgs, state=self.state, tracer=self.tracer, settings=self.settings,
                                 purpose=purpose, schema=schema)
            except LLMError as exc:
                self.tracer.event("warning", phase=purpose, message=f"model call failed: {exc}")
                return None
            try:
                return parse(res.text, schema)
            except ProtocolError as exc:
                self.state.counters.format_repairs += 1
                self.tracer.event("format_repair", phase=purpose, error=str(exc))
                if hasattr(self.llm, "note_failure"):
                    self.llm.note_failure(purpose)
                msgs = msgs + [{"role": "assistant", "content": res.text},
                               {"role": "user", "content": f"FORMAT_ERROR: {exc}. Return only the JSON object."}]
        return None

    # ------------------------------------------------------------------ nodes
    def intake(self) -> None:
        st = self.state
        chunks, skipped = load_materials(Path(st.materials_dir))
        st.chunks[:] = chunks
        st.aliases = sorted(set(st.aliases) | set(self.memory.aliases(st.company)))   # semantic memory read
        past = self.memory.past_cases(st.company)                                      # episodic memory read
        st.config["past_cases"] = past
        self.tracer.event("phase", phase="intake", documents=len({c.doc for c in chunks}), chunks=len(chunks),
                          skipped=skipped, aliases=st.aliases, past_cases=len(past))

    def extract_claims(self) -> None:
        st = self.state
        template = (self.settings.prompts_dir / "extract.md").read_text()
        skill = self.skills.activation_text("claim-extraction")
        raw_claims: list[dict[str, Any]] = []
        for batch in self._chunk_batches(template, skill):
            ids = {c.chunk_id for c in batch}
            payload = [{"chunk_id": c.chunk_id, "doc": c.doc, "location": c.location, "doc_date": c.doc_date,
                        "text": c.text} for c in batch]
            content = render(template, COMPANY=st.company, PRODUCT=st.product, SKILL=skill,
                             CHUNKS_JSON=json.dumps(payload, ensure_ascii=False))
            out = self._model_json(content, EXTRACT_SCHEMA, "extract_claims")
            for c in (out or {}).get("claims", []):
                valid = [i for i in c["source_chunk_ids"] if i in ids]
                if not valid:      # traceability rule: no source location, no claim
                    self.tracer.event("warning", phase="extract_claims", message="dropped claim without valid source",
                                      claim=c["text"][:120])
                    continue
                raw_claims.append({**c, "source_chunk_ids": valid})
        merged: list[dict[str, Any]] = []
        for c in raw_claims:       # near-duplicate merge across documents
            w = set(words(c["text"]))
            dup = next((m for m in merged if len(w & set(words(m["text"]))) / max(1, len(w | set(words(m["text"])))) >= 0.85), None)
            if dup:
                dup["source_chunk_ids"] = sorted(set(dup["source_chunk_ids"]) | set(c["source_chunk_ids"]))
            else:
                merged.append(dict(c))
        st.claims = [Claim(claim_id=f"C{i}", text=c["text"], category=c["category"],
                           source_chunk_ids=c["source_chunk_ids"], product=c.get("product", "") or st.product,
                           status_language=c.get("status_language", ""), materiality=c.get("materiality", "medium"))
                     for i, c in enumerate(merged, 1)]
        self.tracer.event("phase", phase="extract_claims", claims=len(st.claims),
                          by_category=dict(Counter(c.category for c in st.claims)))
        if self.ctrl and self.ctrl.flags["review_claims"]:
            self._review_claims()

    def _review_claims(self) -> None:
        """Reviewer may drop claims, fix wording or change the category before planning."""
        st = self.state
        resp = self.ctrl.request("review_claims", {"claims": [
            {"claim_id": c.claim_id, "text": c.text, "category": c.category, "materiality": c.materiality,
             "status_language": c.status_language,
             "sources": [f"{ch.doc}, {ch.location}" for i in c.source_chunk_ids if (ch := st.chunk(i))]}
            for c in st.claims], "categories": CATEGORIES}) or {}
        edits = {e.get("claim_id"): e for e in resp.get("claims", []) if isinstance(e, dict)}
        kept, dropped = [], []
        for c in st.claims:
            e = edits.get(c.claim_id)
            if e is None:
                kept.append(c)
                continue
            if not e.get("keep", True):
                dropped.append(c.claim_id)
                continue
            if isinstance(e.get("text"), str) and len(e["text"].strip()) >= 8:
                c.text = e["text"].strip()
            if e.get("category") in CATEGORIES:
                c.category = e["category"]
            if e.get("materiality") in ("high", "medium", "low"):
                c.materiality = e["materiality"]
            kept.append(c)
        st.claims = kept
        self.tracer.event("control", event="claims_reviewed", kept=len(kept), dropped=dropped)

    def _chunk_batches(self, template: str, skill: str):
        """Fit chunks into the input budget (Lec 4 'Context budget'): one or more calls per document."""
        overhead = messages_tokens(self.ctx.one_shot(render(template, COMPANY="", PRODUCT="", SKILL=skill, CHUNKS_JSON="")))
        room = max(500, self.ctx.input_budget - overhead - 200)
        by_doc: dict[str, list] = {}
        for c in self.state.chunks:
            by_doc.setdefault(c.doc, []).append(c)
        for chunks in by_doc.values():
            batch, size = [], 0
            for c in chunks:
                t = len(c.text) // 4 + 40
                if batch and size + t > room:
                    yield batch
                    batch, size = [], 0
                batch.append(c)
                size += t
            if batch:
                yield batch

    def _in_focus(self, claim: Claim) -> bool:
        f = self.state.focus
        if not f:
            return True
        cats = set()
        for x in f:
            cats |= set(THEMES.get(x, [x]))
        return claim.category in cats

    def plan(self) -> None:
        st = self.state
        in_scope = [c for c in st.claims if self._in_focus(c)]
        for c in st.claims:
            if not self._in_focus(c):
                st.findings[c.claim_id] = Finding(c.claim_id, "unresolved", [], "Out of scope for this request "
                                                  f"(focus: {', '.join(st.focus)}).", task_id="out_of_scope", verified=True)
        claims_json = [{"claim_id": c.claim_id, "text": c.text, "category": c.category,
                        "status_language": c.status_language, "materiality": c.materiality} for c in in_scope]
        content = render((self.settings.prompts_dir / "plan.md").read_text(),
                         COMPANY=st.company, PRODUCT=st.product, FOCUS=", ".join(st.focus) or "all themes",
                         SKILLS=", ".join(n for n in self.skills.names() if n != "claim-extraction"),
                         MAX_TASKS=str(self.L.max_tasks), CLAIMS_JSON=json.dumps(claims_json, ensure_ascii=False),
                         PAST_CASES=json.dumps(st.config.get("past_cases", [])[:2]))
        out = self._model_json(content, PLAN_SCHEMA, "plan") if in_scope else {"tasks": []}
        valid_ids = {c.claim_id for c in in_scope}
        covered: set[str] = set()
        tasks: list[ResearchTask] = []
        for t in (out or {}).get("tasks", []):
            ids = [i for i in t["claim_ids"] if i in valid_ids and i not in covered]
            if not ids:
                continue
            skill = t["skill"] if self.skills.get(t["skill"]) else DEFAULT_SKILL.get(st.claim(ids[0]).category,
                                                                                    "commercial-signals")
            covered |= set(ids)
            tasks.append(ResearchTask("", ids, t["question"], skill, t["priority"]))
        missing = [c for c in in_scope if c.claim_id not in covered]
        if missing:   # coverage check: every material claim gets a task
            self.tracer.event("plan", message="planner missed claims; added fallback tasks",
                              claims=[c.claim_id for c in missing])
            tasks += [ResearchTask("", [c.claim_id], f"Check the public evidence for: {c.text[:160]}",
                                   DEFAULT_SKILL.get(c.category, "commercial-signals"), 2) for c in missing]
        tasks.sort(key=lambda t: t.priority)
        for i, t in enumerate(tasks, 1):
            t.task_id = f"T{i}"
        st.tasks = tasks[: self.L.max_tasks]
        for t in tasks[self.L.max_tasks:]:
            for cid in t.claim_ids:
                st.findings[cid] = Finding(cid, "unresolved", [], "Not researched: run task budget reached.",
                                           open_questions=[t.question], task_id=t.task_id, verified=True)
        self.tracer.event("plan", tasks=[{"task_id": t.task_id, "claims": t.claim_ids, "skill": t.skill,
                                          "priority": t.priority} for t in st.tasks])
        if self.ctrl and self.ctrl.flags["review_plan"]:
            self._review_plan()

    def _review_plan(self) -> None:
        """Reviewer may reorder, drop or edit tasks (question, skill, priority) before research starts."""
        st = self.state
        skills = [n for n in self.skills.names() if n != "claim-extraction"]
        resp = self.ctrl.request("review_plan", {
            "tasks": [{"task_id": t.task_id, "claim_ids": t.claim_ids, "question": t.question, "skill": t.skill,
                       "priority": t.priority,
                       "claims": [{"claim_id": c, "text": st.claim(c).text, "category": st.claim(c).category}
                                  for c in t.claim_ids if st.claim(c)]} for t in st.tasks],
            "skills": skills}) or {}
        by_id = {t.task_id: t for t in st.tasks}
        new: list[ResearchTask] = []
        removed: list[str] = []
        for e in resp.get("tasks", [t.__dict__ for t in st.tasks]):
            t = by_id.pop(e.get("task_id"), None) if isinstance(e, dict) else None
            if t is None:
                continue
            if not e.get("keep", True):
                removed.append(t.task_id)
                for cid in t.claim_ids:
                    st.findings[cid] = Finding(cid, "unresolved", [], "Not researched: removed from the plan by "
                                               "the reviewer.", task_id="removed", verified=True)
                continue
            if isinstance(e.get("question"), str) and len(e["question"].strip()) >= 8:
                t.question = e["question"].strip()
            if e.get("skill") in skills:
                t.skill = e["skill"]
            if e.get("priority") in (1, 2, 3, "1", "2", "3"):
                t.priority = int(e["priority"])
            new.append(t)
        new += list(by_id.values())          # tasks the response did not mention keep their place at the end
        for i, t in enumerate(new, 1):
            t.task_id = f"T{i}"
        st.tasks = new
        self.tracer.event("control", event="plan_reviewed", removed=removed,
                          tasks=[{"task_id": t.task_id, "claims": t.claim_ids, "skill": t.skill} for t in new])

    def research(self) -> None:
        st = self.state
        h = ResearchHarness(llm=self.llm, gateway=self.gateway, ctx=self.ctx, skills=self.skills, state=st,
                            memory=self.memory, tracer=self.tracer, settings=self.settings, ask_fn=self.ask_fn,
                            run_deadline=self.deadline, ctrl=self.ctrl)
        for task in st.tasks:
            if task.status != "pending":
                continue          # resume: skip finished tasks
            if self.ctrl:
                self.ctrl.gate("task", task_id=task.task_id)
            if self._now() > self.deadline:
                task.status, task.stop_reason = "unresolved", "run time budget exhausted"
                for cid in task.claim_ids:
                    st.findings[cid] = Finding(cid, "unresolved", [], "Not researched: run time budget exhausted.",
                                               open_questions=[task.question], task_id=task.task_id, verified=True)
                continue
            claim = st.claim(task.claim_ids[0])
            self.tracer.event("phase", phase="research", task_id=task.task_id, skill=task.skill,
                              claim=claim.text[:100] if claim else "")
            for f in h.run(task):
                st.findings[f.claim_id] = f
            self.checkpoint()

    def reconcile(self) -> None:
        """Cross-document checks in Python + collation of questions for specialist review."""
        st = self.state
        pct = re.compile(r"(\d+(?:\.\d+)?)\s*%")
        for metric in ("sensitivity", "specificity", "accuracy"):
            vals: dict[str, list[str]] = {}
            for ch in st.chunks:
                for sent in re.split(r"(?<=[.!?])\s+", ch.text):
                    if metric in sent.lower() and (m := pct.search(sent)):
                        vals.setdefault(m.group(1), []).append(f"{ch.doc} ({ch.chunk_id})")
            if len(vals) > 1:
                rec = {"metric": metric, "values": [{"value": f"{k}%", "sources": v} for k, v in vals.items()]}
                st.cross_claim_conflicts.append(rec)
                note = (f"Company materials report different {metric} values: " +
                        "; ".join(f"{k}% in {', '.join(v)}" for k, v in vals.items()))
                for f in st.findings.values():
                    c = st.claim(f.claim_id)
                    if c and metric in c.text.lower() and not any(metric in x and "different" in x for x in f.conflicts):
                        f.conflicts.append(note)
        for f in st.findings.values():
            if f.task_id == "out_of_scope":
                continue
            for q in f.open_questions:
                st.review_questions.append({"claim_id": f.claim_id, "question": q, "source": "finding"})
            if f.identity_checked and f.identity_confirmed is not True and f.verdict != "unresolved" \
                    and not f.open_questions:
                st.review_questions.append({"claim_id": f.claim_id, "source": "finding",
                                            "question": "Confirm the entity match between the company and the public record."})
        self.tracer.event("phase", phase="reconcile", cross_claim_conflicts=len(st.cross_claim_conflicts),
                          review_questions=len(st.review_questions))

    def brief(self) -> None:
        st = self.state
        summary = self.tracer.summary()
        md = brief_mod.render(st, summary)
        (self.run_dir / "brief.md").write_text(md)
        (self.run_dir / "record.json").write_text(st.to_json())
        (self.run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        self.memory.record_case(st.run_id, st.company, {   # episodic memory write
            "verdicts": dict(Counter(f.verdict for f in st.findings.values())),
            "open_questions": [q["question"] for q in st.review_questions][:5]})
        self.tracer.event("phase", phase="brief", path=str(self.run_dir / "brief.md"))


def _db_notes(settings) -> str:
    from .user_settings import load
    return load(settings.runs_dir)["database_notes"]


def load_for_resume(settings, run_id: str) -> RunState:
    """Load a checkpoint and restore the configuration the run started with (provider, backend, limits)."""
    mem = Memory(settings.db_path)
    ck = mem.load_checkpoint(run_id)
    mem.close()
    if ck is None:
        raise SystemExit(f"no checkpoint for run {run_id}")
    st = RunState.from_json(ck[1])
    settings.provider = st.config.get("provider", settings.provider)
    settings.model = st.config.get("model", settings.model)
    settings.backend = st.config.get("backend", settings.backend)
    settings.tool_transport = st.config.get("tool_transport", settings.tool_transport)
    settings.allow_cloud = bool(st.config.get("allow_cloud", settings.allow_cloud))
    settings.use_database = bool(st.config.get("use_database", settings.use_database))
    for k, v in (st.config.get("limits") or {}).items():
        if hasattr(settings.limits, k):
            setattr(settings.limits, k, v)
    return st


def resume(settings, run_id: str, ask_fn=None, echo=True) -> RunState:
    st = load_for_resume(settings, run_id)
    if st.phase == "done":
        print(f"[medscout] run {run_id} already finished")
        return st
    wf = Workflow(settings, st, ask_fn=ask_fn, echo=echo)
    return wf.run()
