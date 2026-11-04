"""Run controller: reviewer gates, approvals, edits, notes, skip, pause and stop/resume."""

import threading
import time
import unittest

from medscout.control import RunController
from medscout.workflow import Workflow, load_for_resume

from .helpers import new_state, temp_settings


def run_with_reviewer(wf, ctrl, answer, timeout=30):
    """Run the workflow in a thread; answer each pending request with answer(kind, payload)."""
    t = threading.Thread(target=wf.run, daemon=True)
    t.start()
    seen = []
    t_end = time.time() + timeout
    while t.is_alive() and time.time() < t_end:
        p = ctrl.pending
        if p is not None:
            seen.append(p.kind)
            resp = answer(p.kind, p.payload)
            if resp is not None:
                ctrl.respond(p.request_id, resp)
        time.sleep(0.01)
    t.join(1)
    assert not t.is_alive(), "run did not finish"
    return seen


def build(focus=("regulatory",), case="nordleaf", **flags):
    s = temp_settings()
    st, cdir = new_state(case, focus=list(focus))
    ctrl = RunController(**flags)
    wf = Workflow(s, st, case_dir=cdir, echo=False, ctrl=ctrl,
                  ask_fn=lambda q, o: ctrl.request("ask_user", {"question": q, "options": o}).get("answer"))
    ctrl.emit = wf.tracer.event
    return s, st, ctrl, wf


def events(wf, name):
    return [e for e in wf.tracer.events if e["kind"] == "control" and e.get("event") == name]


class TestController(unittest.TestCase):
    def test_review_claims_and_plan(self):
        s, st, ctrl, wf = build(focus=(), review_claims=True, review_plan=True, ask_reviewer=False)

        def answer(kind, p):
            if kind == "review_claims":   # drop every commercial claim, recategorise nothing
                return {"claims": [{**c, "keep": c["category"] != "commercial"} for c in p["claims"]]}
            if kind == "review_plan":     # reverse the order, drop the last task, rewrite one question
                tasks = list(reversed(p["tasks"]))
                tasks[0] = {**tasks[0], "question": "Reviewer-edited research question for this task."}
                tasks[-1] = {**tasks[-1], "keep": False}
                return {"tasks": tasks}
            return {}

        seen = run_with_reviewer(wf, ctrl, answer)
        self.assertEqual(seen[:2], ["review_claims", "review_plan"])
        self.assertFalse(any(c.category == "commercial" for c in st.claims))
        self.assertEqual(st.tasks[0].question, "Reviewer-edited research question for this task.")
        self.assertTrue(any(f.task_id == "removed" for f in st.findings.values()))
        self.assertEqual(st.phase, "done")

    def test_tool_approval_reject_then_edit(self):
        s, st, ctrl, wf = build(approve_tools=True, ask_reviewer=False)
        n = {"approve": 0}

        def answer(kind, p):
            if kind != "approve_tools":
                return {}
            n["approve"] += 1
            if n["approve"] == 1:
                return {"decision": "reject", "note": "search by product name instead"}
            calls = [{**c, "arguments": {**c["arguments"], "limit": 2}} if c["tool"] == "fda_510k_search" else c
                     for c in p["calls"]]
            return {"decision": "approve", "calls": calls}

        run_with_reviewer(wf, ctrl, answer)
        self.assertEqual(len(events(wf, "tools_rejected")), 1)
        self.assertTrue(events(wf, "tools_edited"))
        edited = [e for e in wf.tracer.events if e["kind"] == "tool_call" and e["arguments"].get("limit") == 2]
        self.assertTrue(edited)
        self.assertEqual(st.phase, "done")

    def test_ask_user_answered_in_ui(self):
        s, st, ctrl, wf = build(ask_reviewer=True)
        seen = run_with_reviewer(wf, ctrl, lambda k, p: {"answer": "yes"} if k == "ask_user" else {})
        self.assertIn("ask_user", seen)
        self.assertIn("Nordleaf Cardio Technologies, Inc.", st.aliases)

    def test_step_mode_notes_and_skip(self):
        s, st, ctrl, wf = build(step_mode=True, ask_reviewer=False)
        ctrl.add_note("Prefer the K-number when one is stated.", task_id="T1")
        steps = {"n": 0}

        def answer(kind, p):
            if kind == "step":
                steps["n"] += 1
                if p.get("task_id") == "T2":
                    ctrl.skip("T2")
                return {"continue": True}
            return {}

        run_with_reviewer(wf, ctrl, answer)
        self.assertGreater(steps["n"], 3)
        self.assertTrue(events(wf, "note_delivered"))
        t2 = next(t for t in st.tasks if t.task_id == "T2")
        self.assertEqual(t2.stop_reason, "skipped by reviewer")

    def test_pause_time_is_excluded_from_deadlines(self):
        s, st, ctrl, wf = build(ask_reviewer=False)
        s.limits.task_deadline_s = 1.0
        ctrl.pause()
        threading.Timer(1.5, ctrl.resume).start()
        wf.run()
        self.assertFalse(any("time budget" in t.stop_reason for t in st.tasks))
        self.assertGreaterEqual(ctrl.paused_total, 1.4)

    def test_stop_then_resume(self):
        s, st, ctrl, wf = build(step_mode=True, ask_reviewer=False)
        steps = {"n": 0}

        def answer(kind, p):
            steps["n"] += 1
            if steps["n"] == 3:
                ctrl.stop()
                return None
            return {"continue": True}

        run_with_reviewer(wf, ctrl, answer)
        self.assertEqual(st.stop_reason, "stopped by reviewer")
        self.assertNotEqual(st.phase, "done")
        st2 = load_for_resume(s, st.run_id)
        Workflow(s, st2, echo=False).run()
        self.assertEqual(st2.phase, "done")
        self.assertEqual(len(st2.findings), len(st2.claims))


if __name__ == "__main__":
    unittest.main()
