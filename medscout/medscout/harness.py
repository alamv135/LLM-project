"""Bounded research loop for one task (Lec 4 "Bounded agent loop" / "Harness loop").

for step in range(MAX_STEPS):
    deadline?            -> handoff("time budget")
    decide(build_context(state))
    tool_calls           -> validate, execute in parallel, observe, update state
    ask_user             -> human answer (or NO_HUMAN_AVAILABLE) becomes an observation
    finish               -> runtime verification; one bounded revision; else downgrade
    malformed output     -> one bounded format repair; else stop
return handoff("step budget")

Every new observation can change the next model decision (ReAct).
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from .context import ContextBuilder, observation_message
from .llm import LLMError, call_model
from .protocol import ProtocolError, parse_decision
from .state import Finding, ResearchTask, RunState, SourceChunk
from .verify import verify_assessments

AskFn = Callable[[str, list[str]], "str | None"]

INSTRUCTION = ("Decide the next action for this task. Return exactly one JSON object: "
               '{"action":"tool_calls",...} | {"action":"finish",...} | {"action":"ask_user",...}.')


class ResearchHarness:
    def __init__(self, *, llm, gateway, ctx: ContextBuilder, skills, state: RunState, memory, tracer,
                 settings, ask_fn: AskFn | None, run_deadline: float, ctrl=None):
        self.llm, self.gw, self.ctx, self.skills = llm, gateway, ctx, skills
        self.state, self.memory, self.tracer, self.settings = state, memory, tracer, settings
        self.L = settings.limits
        self.ask_fn = ask_fn
        self.run_deadline = run_deadline
        self.ctrl = ctrl            # optional RunController (web UI); None for CLI runs

    def _now(self) -> float:
        return self.ctrl.now() if self.ctrl else time.monotonic()

    def _human_available(self) -> bool:
        if self.ctrl:
            return self.ctrl.flags["ask_reviewer"] and self.ask_fn is not None
        return bool(self.ask_fn and self.settings.interactive)

    # ------------------------------------------------------------------ brief
    def _task_brief(self, task: ResearchTask, company_ev: dict[str, str]) -> str:
        claims = [self.state.claim(c) for c in task.claim_ids]
        payload = {
            "task_id": task.task_id, "question": task.question, "skill": task.skill,
            "company": self.state.company, "product": self.state.product, "aliases": self.state.aliases,
            "claims": [{"claim_id": c.claim_id, "text": c.text, "category": c.category,
                        "status_language": c.status_language, "source_chunk_ids": c.source_chunk_ids}
                       for c in claims if c],
            "company_evidence_ids": list(company_ev.values()),
        }
        excerpts = []
        for chunk_id, eid in company_ev.items():
            ch = self.state.chunk(chunk_id)
            if ch:
                excerpts.append(f"[{eid}] {ch.doc}, {ch.location} (company-reported): {ch.text[:600]}")
        return "\n".join([
            "PHASE: research",
            "TASK_JSON:", json.dumps(payload, ensure_ascii=False), "END_TASK",
            "",
            self.skills.activation_text(task.skill),
            "",
            "SOURCE_EXCERPTS (company materials; cite their evidence ids but they are NOT independent support):",
            *excerpts,
        ])

    def _tail(self, step: int, used: int, t_end: float, asked: bool, last: bool) -> str:
        ws = {"task_step": step + 1, "max_steps": self.L.max_steps_per_task, "tool_calls_used": used,
              "tool_calls_remaining": max(0, self.L.max_tool_calls_per_task - used),
              "max_calls_per_decision": self.L.max_calls_per_batch,
              "seconds_left": max(0, round(t_end - self._now())), "already_asked_user": asked}
        extra = " This is the LAST step: you must return action 'finish' now." if last else ""
        return f"WORKING_STATE: {json.dumps(ws)}\nINSTRUCTION: {INSTRUCTION}{extra}"

    # ------------------------------------------------------------------ run
    def run(self, task: ResearchTask) -> list[Finding]:
        st, L = self.state, self.L
        chunks: list[SourceChunk] = [ch for cid in task.claim_ids for ch_id in st.claim(cid).source_chunk_ids
                                     if (ch := st.chunk(ch_id))]
        company_ev = self.gw.register_company_chunks(chunks)
        observed: set[str] = set(company_ev.values())
        brief = self._task_brief(task, company_ev)
        history: list[dict[str, Any]] = []
        t_end = min(self._now() + L.task_deadline_s, self.run_deadline)
        used = repairs = revisions = asks = 0
        allowed = set(self.gw.specs)

        for step in range(L.max_steps_per_task):
            if self.ctrl:
                if self.ctrl.should_skip(task.task_id):
                    return self._handoff(task, "skipped by reviewer", company_ev)
                self.ctrl.gate("decision", task_id=task.task_id, step=step + 1, tool_calls_used=used)
                if self.ctrl.should_skip(task.task_id):
                    return self._handoff(task, "skipped by reviewer", company_ev)
                for note in self.ctrl.take_notes(task.task_id):   # reviewer guidance enters the context
                    history.append({"role": "user", "content": f"REVIEWER_NOTE: {note}"})
                    self.tracer.event("control", event="note_delivered", task_id=task.task_id, text=note[:200])
            if self._now() > t_end:
                return self._handoff(task, "time budget exhausted", company_ev)
            last = step == L.max_steps_per_task - 1
            msgs, info = self.ctx.build(brief, history, self._tail(step, used, t_end, asks > 0, last))
            try:
                res = call_model(self.llm, msgs, state=st, tracer=self.tracer, settings=self.settings,
                                 purpose="research", task_id=task.task_id, step=step)
            except LLMError as exc:
                return self._handoff(task, f"model call failed: {exc}", company_ev)
            self.tracer.event("context", task_id=task.task_id, step=step, **info)
            try:
                d = parse_decision(res.text, allowed, L.max_calls_per_batch)
            except ProtocolError as exc:
                if repairs < L.max_format_repairs:
                    repairs += 1
                    st.counters.format_repairs += 1
                    self.tracer.event("format_repair", task_id=task.task_id, step=step, error=str(exc))
                    history += [{"role": "assistant", "content": res.text},
                                {"role": "user", "content": f"FORMAT_ERROR: {exc}. Follow the OUTPUT CONTRACT."}]
                    continue
                return self._handoff(task, f"malformed model output after repair: {exc}", company_ev)
            history.append({"role": "assistant", "content": res.text})
            self.tracer.event("decision", task_id=task.task_id, step=step, action=d["action"],
                              summary=d.get("thought", "")[:160])

            if d["action"] == "tool_calls":
                remaining = L.max_tool_calls_per_task - used
                if remaining <= 0:
                    history.append({"role": "user", "content": "TOOL_BUDGET_EXHAUSTED: no more tool calls; "
                                                               "finish with the evidence you have."})
                    continue
                calls = d["calls"][:remaining]
                if self.ctrl and self.ctrl.flags["approve_tools"]:
                    calls, note = self._approve(task, step, calls, d.get("thought", ""), allowed)
                    if not calls:
                        history.append({"role": "user", "content": "REVIEWER_REJECTED_TOOL_CALLS: "
                                        + (note or "the reviewer did not approve these calls")
                                        + ". Choose a different action."})
                        continue
                obs = self.gw.run_batch(calls, task_id=task.task_id, step=step)
                used += len(calls)
                for o in obs:
                    observed.update(o.evidence_ids)
                history.append(observation_message([o.for_model(L.max_records_per_observation) for o in obs]))

            elif d["action"] == "ask_user":
                asks += 1
                history.append({"role": "user", "content": self._ask(task, d, asks)})

            else:  # finish
                problems = verify_assessments(d["assessments"], st, task, observed)
                self.tracer.event("verify", task_id=task.task_id, step=step, ok=not problems, problems=problems)
                if problems and revisions < L.max_verify_revisions and not last:
                    revisions += 1
                    st.counters.verify_rejections += 1
                    history.append({"role": "user", "content": "VERIFICATION_FAILED:\n- " + "\n- ".join(problems) +
                                                               "\nRevise the assessment (you may call tools first)."})
                    continue
                return self._accept(task, d["assessments"], problems, company_ev)
        return self._handoff(task, "step budget exhausted", company_ev)

    # ------------------------------------------------------------------ human in the loop
    def _approve(self, task, step, calls, thought, allowed):
        """Reviewer approves, edits or rejects a proposed tool batch before it runs."""
        resp = self.ctrl.request("approve_tools", {"task_id": task.task_id, "step": step + 1,
                                                   "thought": thought, "calls": calls}) or {}
        if resp.get("decision") != "approve":
            self.tracer.event("control", event="tools_rejected", task_id=task.task_id, note=resp.get("note", ""))
            return [], resp.get("note", "")
        edited = []
        for c in resp.get("calls") or calls:
            if isinstance(c, dict) and c.get("tool") in allowed and isinstance(c.get("arguments"), dict):
                edited.append({"tool": c["tool"], "arguments": c["arguments"]})
        edited = edited[: self.L.max_calls_per_batch]
        if edited != calls:
            self.tracer.event("control", event="tools_edited", task_id=task.task_id, calls=edited)
        return edited, ""

    def _ask(self, task: ResearchTask, d: dict[str, Any], asks: int) -> str:
        q = d["question"]
        if asks > 1:
            return "ASK_LIMIT: only one question per task; proceed and record the doubt as an open question."
        answer = self.ask_fn(q, d.get("options", [])) if self._human_available() else None
        if isinstance(answer, str) and not answer.strip():
            answer = None
        rec = {"task_id": task.task_id, "question": q, "answer": answer, "alias_candidate": d.get("alias_candidate")}
        self.tracer.event("ask_user", **rec)
        self.state.human_answers.append(rec)
        if answer is None:
            return ("NO_HUMAN_AVAILABLE: no reviewer is available. Proceed without confirmation, set "
                    "identity_confirmed to null and list the doubt in open_questions.")
        alias = d.get("alias_candidate")
        if alias and answer.strip().lower() in {"y", "yes", "same", "true"}:
            # validated memory write: only a human-confirmed alias becomes semantic memory
            self.memory.add_alias(self.state.company, alias, source=f"run {self.state.run_id}", confirmed_by="user")
            if alias not in self.state.aliases:
                self.state.aliases.append(alias)
        return f"HUMAN_ANSWER: {answer}"

    # ------------------------------------------------------------------ outcomes
    def _accept(self, task, assessments, problems, company_ev) -> list[Finding]:
        out = []
        for a in assessments:
            if a["claim_id"] not in task.claim_ids:
                continue
            f = Finding(claim_id=a["claim_id"], verdict=a["verdict"], evidence_ids=a.get("evidence_ids", []),
                        rationale=a["rationale"], conflicts=a.get("conflicts", []),
                        open_questions=a.get("open_questions", []), identity_confirmed=a.get("identity_confirmed"),
                        identity_checked="identity_confirmed" in a, task_id=task.task_id, verified=not problems)
            mine = [p for p in problems if p.startswith(a["claim_id"] + ":")]
            if mine:   # still ungrounded after bounded revision: do not publish the verdict
                f.rationale = f"[runtime downgrade from '{f.verdict}': {'; '.join(mine)}] {f.rationale}"
                f.verdict = "unresolved"
                f.evidence_ids = [e for e in f.evidence_ids if e in self.state.evidence]
                f.open_questions = f.open_questions or ["Verify this claim manually."]
            out.append(f)
        covered = {f.claim_id for f in out}
        out += [self._stopped(c, task, "no assessment returned", company_ev) for c in task.claim_ids if c not in covered]
        task.status = "done"
        task.stop_reason = "finished" if not problems else "finished with runtime downgrade"
        return out

    def _stopped(self, claim_id, task, reason, company_ev) -> Finding:
        ch_ids = self.state.claim(claim_id).source_chunk_ids
        return Finding(claim_id=claim_id, verdict="unresolved", evidence_ids=[company_ev[c] for c in ch_ids if c in company_ev],
                       rationale=f"Research stopped: {reason}.", open_questions=[task.question],
                       task_id=task.task_id, verified=True)

    def _handoff(self, task, reason, company_ev) -> list[Finding]:
        self.tracer.event("stop", task_id=task.task_id, reason=reason)
        task.status, task.stop_reason = "unresolved", reason
        return [self._stopped(c, task, reason, company_ev) for c in task.claim_ids]
