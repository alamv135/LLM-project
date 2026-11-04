"""Agent behaviour with the mock model: workflow, verification, context, memory, recovery."""

import json
import unittest

from medscout.context import ContextBuilder, observation_message
from medscout.config import Limits
from medscout.memory import Memory
from medscout.state import Claim, Evidence, ResearchTask
from medscout.verify import verify_assessments
from medscout.workflow import Workflow, resume

from .helpers import env, new_state, temp_settings


def verdicts(st):
    return {c.text: st.findings[c.claim_id].verdict for c in st.claims}


def find(st, needle):
    c = next(c for c in st.claims if needle.lower() in c.text.lower())
    return c, st.findings[c.claim_id]


class TestWorkflow(unittest.TestCase):
    def test_end_to_end_nordleaf(self):
        s = temp_settings()
        st, cdir = new_state()
        wf = Workflow(s, st, case_dir=cdir, echo=False)
        wf.run()
        self.assertEqual(st.phase, "done")
        self.assertEqual(find(st, "FDA approved")[1].verdict, "partially_supported")
        self.assertEqual(find(st, "completed enrollment")[1].verdict, "possibly_contradicted")
        self.assertEqual(find(st, "Medicare")[1].verdict, "company_reported_only")
        # every finding is grounded in evidence that exists, and every claim has a source location
        for f in st.findings.values():
            self.assertTrue(all(e in st.evidence for e in f.evidence_ids))
        self.assertTrue(all(c.source_chunk_ids and st.chunk(c.source_chunk_ids[0]) for c in st.claims))
        brief = (wf.run_dir / "brief.md").read_text()
        self.assertIn("SYNTHETIC DATA", brief)
        self.assertIn("K251234", brief)
        self.assertTrue((wf.run_dir / "trace.jsonl").exists())

    def test_changed_request_focus_limits_research(self):
        s = temp_settings()
        st, cdir = new_state(focus=["regulatory"])
        Workflow(s, st, case_dir=cdir, echo=False).run()
        researched = {st.claim(c).category for t in st.tasks for c in t.claim_ids}
        self.assertEqual(researched, {"regulatory"})
        self.assertEqual(find(st, "Medicare")[1].task_id, "out_of_scope")

    def test_failure_case_reports_honest_gaps(self):
        s = temp_settings()
        s.limits.retry_base_delay_s = 0.01
        st, cdir = new_state("quillon")
        with env(MEDSCOUT_FAULTS="clinicaltrials_search=unavailable*20"):
            Workflow(s, st, case_dir=cdir, echo=False).run()
        self.assertEqual(find(st, "300-patient")[1].verdict, "unresolved")
        self.assertTrue(all(f.verdict in ("unresolved", "company_reported_only") for f in st.findings.values()))

    def test_format_repair_and_verification_revision(self):
        s = temp_settings()
        st, cdir = new_state(focus=["regulatory"])
        with env(MEDSCOUT_MOCK_FAULTS="malformed:1,ungrounded:1"):
            Workflow(s, st, case_dir=cdir, echo=False).run()
        self.assertEqual(st.counters.format_repairs, 1)
        self.assertEqual(st.counters.verify_rejections, 1)
        self.assertEqual(find(st, "FDA approved")[1].verdict, "partially_supported")   # recovered

    def test_step_budget_handoff(self):
        s = temp_settings()
        s.limits.max_steps_per_task = 1      # the mock needs >1 step, so every task hands off
        st, cdir = new_state(focus=["regulatory"])
        Workflow(s, st, case_dir=cdir, echo=False).run()
        self.assertTrue(all(t.status == "unresolved" and "step budget" in t.stop_reason for t in st.tasks))
        self.assertTrue(all(f.verdict == "unresolved" for f in st.findings.values()))

    def test_resume_from_checkpoint(self):
        s = temp_settings()
        st, cdir = new_state(focus=["regulatory"])
        wf = Workflow(s, st, case_dir=cdir, echo=False)
        for node in ("intake", "extract_claims", "plan"):   # simulate a crash after planning
            getattr(wf, node)()
        st.phase = "research"
        wf.checkpoint()
        wf.gateway.close()
        done = resume(s, st.run_id, echo=False)
        self.assertEqual(done.phase, "done")
        self.assertEqual(len(done.findings), len(done.claims))

    def test_human_confirmed_alias_becomes_memory(self):
        s = temp_settings(interactive=True)
        st, cdir = new_state(focus=["regulatory"])
        Workflow(s, st, case_dir=cdir, ask_fn=lambda q, o: "yes", echo=False).run()
        self.assertIn("Nordleaf Cardio Technologies, Inc.", Memory(s.db_path).aliases("Nordleaf Cardio"))
        self.assertTrue(find(st, "K251234")[1].identity_confirmed)
        # next run reads the alias from semantic memory and does not need to ask again
        st2, _ = new_state(focus=["regulatory"])
        asked = []
        Workflow(s, st2, case_dir=cdir, ask_fn=lambda q, o: asked.append(q) or "yes", echo=False).run()
        self.assertEqual(asked, [])


class TestVerifier(unittest.TestCase):
    def setUp(self):
        st, _ = new_state()
        st.claims = [Claim("C1", "PulseWeave is FDA approved for AF.", "regulatory", ["D1#1"])]
        st.evidence = {
            "E1": Evidence("E1", "search_company_docs", "Company material", "D1#1", "", "", {}, False),
            "E2": Evidence("E2", "fda_510k_search", "openFDA 510(k)", "K251234", "", "", {}, True),
        }
        self.st = st
        self.task = ResearchTask("T1", ["C1"], "q", "regulatory-check")

    def check(self, **a):
        base = {"claim_id": "C1", "verdict": "publicly_supported", "evidence_ids": ["E2"], "rationale": "x" * 12}
        return verify_assessments([{**base, **a}], self.st, self.task, {"E1", "E2"})

    def test_unknown_and_unseen_ids(self):
        self.assertTrue(any("unknown" in p for p in self.check(verdict="partially_supported", evidence_ids=["E9"])))

    def test_substantive_verdict_needs_independent_evidence(self):
        self.assertTrue(any("independent" in p for p in self.check(verdict="partially_supported", evidence_ids=["E1"])))

    def test_510k_is_not_approval_guardrail(self):
        self.assertTrue(any("510(k)" in p for p in self.check()))
        self.assertEqual(self.check(verdict="partially_supported", conflicts=["cleared not approved"]), [])

    def test_contradiction_needs_conflict_and_unresolved_needs_question(self):
        self.assertTrue(self.check(verdict="possibly_contradicted"))
        self.assertTrue(self.check(verdict="unresolved", evidence_ids=[]))


class TestContext(unittest.TestCase):
    def test_stable_prefix_and_compression_keep_evidence_ids(self):
        ctx = ContextBuilder("SYSTEM RULES", Limits(context_window=1200, reserved_output=200))
        big = [{"call_id": f"c{i}", "tool": "fda_510k_search", "arguments": {}, "ok": True, "total": 1,
                "records": [{"evidence_id": f"E{i}", "device_name": "x" * 2500}]} for i in range(3)]
        hist = [observation_message([o]) for o in big]
        msgs, info = ctx.build("TASK BRIEF", hist, "WORKING_STATE: {}")
        self.assertEqual(msgs[0]["content"], "SYSTEM RULES")          # stable prefix first
        self.assertTrue(msgs[-1]["content"].endswith("WORKING_STATE: {}"))   # volatile tail last
        self.assertGreaterEqual(info["compressed_observations"], 1)
        self.assertIn('"E0"', msgs[2]["content"])                     # ids survive compression
        self.assertIn("get_evidence", msgs[2]["content"])


if __name__ == "__main__":
    unittest.main()
