"""Tool contracts, mirror backend, gateway retries / fault classification / evidence provenance."""

import unittest

from medscout.memory import Memory
from medscout.skills import SkillLibrary
from medscout.state import RunState
from medscout.tools import ToolContext, ToolGateway, build_specs
from medscout.tools.openfda import _date
from medscout.trace import Tracer

from .helpers import CASES, env, temp_settings


def gateway(settings=None, case="nordleaf"):
    s = settings or temp_settings()
    st = RunState.new("t", "")
    ctx = ToolContext("mirror", CASES / case / "public_mirror", evidence=st.evidence,
                      skills=SkillLibrary(s.skills_dir), cache=Memory(s.db_path))
    s.limits.retry_base_delay_s = 0.01
    return ToolGateway(build_specs(), ctx, st, s.limits, Tracer(None, st.run_id)), st


class TestTools(unittest.TestCase):
    def test_openfda_date_normalisation(self):
        self.assertEqual(_date("20250220"), "2025-02-20")
        self.assertEqual(_date("2025-02-20"), "2025-02-20")

    def test_name_variant_matching_and_identity_trap(self):
        gw, st = gateway()
        o = gw.run_batch([{"tool": "fda_510k_search", "arguments": {"applicant": "Nordleaf Cardio"}}])[0]
        self.assertTrue(o.ok)
        self.assertEqual([r["k_number"] for r in o.data["records"]], ["K251234"])   # not Nordleaf Surgical
        o2 = gw.run_batch([{"tool": "fda_510k_search", "arguments": {"applicant": "Nordleaf"}}])[0]
        self.assertEqual(len(o2.data["records"]), 2)                                # both share the word
        gw.close()

    def test_evidence_provenance_and_dedup(self):
        gw, st = gateway()
        a = gw.run_batch([{"tool": "clinicaltrials_search", "arguments": {"sponsor": "Nordleaf Cardio"}}])[0]
        b = gw.run_batch([{"tool": "clinicaltrials_search", "arguments": {"nct_id": "NCT09912345"}}])[0]
        self.assertEqual(a.evidence_ids, b.evidence_ids)                 # same record -> same evidence id
        ev = st.evidence[a.evidence_ids[0]]
        self.assertEqual(ev.identifier, "NCT09912345")
        self.assertTrue(ev.independent and ev.synthetic)
        self.assertIn("clinicaltrials.gov/study/NCT09912345", ev.url)
        gw.close()

    def test_input_schema_rejects_bad_arguments(self):
        gw, _ = gateway()
        bad = gw.run_batch([{"tool": "fda_510k_search", "arguments": {"k_number": "12345"}},
                            {"tool": "fda_510k_search", "arguments": {}},
                            {"tool": "pubmed_search", "arguments": {"query": "x", "sql": "1"}}])
        self.assertTrue(all(not o.ok and o.error_kind == "permanent" for o in bad))
        gw.close()

    def test_transient_fault_is_retried_then_recovers(self):
        with env(MEDSCOUT_FAULTS="pubmed_search=timeout*1"):
            gw, _ = gateway()
        o = gw.run_batch([{"tool": "pubmed_search", "arguments": {"query": "PulseWeave validation"}}])[0]
        self.assertTrue(o.ok)
        self.assertEqual(o.attempts, 2)
        gw.close()

    def test_transient_fault_gives_up_after_bounded_retries(self):
        with env(MEDSCOUT_FAULTS="pubmed_search=timeout*5"):
            gw, st = gateway()
        o = gw.run_batch([{"tool": "pubmed_search", "arguments": {"query": "x"}}])[0]
        self.assertFalse(o.ok)
        self.assertEqual(o.attempts, 1 + gw.limits.tool_max_retries)
        self.assertIn("gave up", o.error)
        gw.close()

    def test_unavailable_is_not_retried(self):
        with env(MEDSCOUT_FAULTS="clinicaltrials_search=unavailable*5"):
            gw, _ = gateway()
        o = gw.run_batch([{"tool": "clinicaltrials_search", "arguments": {"sponsor": "x"}}])[0]
        self.assertEqual((o.ok, o.error_kind, o.attempts), (False, "unavailable", 1))
        gw.close()

    def test_missing_mirror_reports_unavailable(self):
        s = temp_settings()
        st = RunState.new("t", "")
        gw = ToolGateway(build_specs(), ToolContext("mirror", None, evidence=st.evidence), st, s.limits,
                         Tracer(None, "x"))
        o = gw.run_batch([{"tool": "fda_pma_search", "arguments": {"applicant": "x"}}])[0]
        self.assertEqual(o.error_kind, "unavailable")
        gw.close()

    def test_parallel_batch_keeps_call_ids(self):
        gw, _ = gateway()
        obs = gw.run_batch([{"tool": "fda_510k_search", "arguments": {"product_code": "ZXA"}},
                            {"tool": "fda_classification_lookup", "arguments": {"product_code": "ZXA"}},
                            {"tool": "pubmed_search", "arguments": {"query": "ablation monitoring"}}])
        self.assertEqual([o.tool for o in obs], ["fda_510k_search", "fda_classification_lookup", "pubmed_search"])
        self.assertEqual(len({o.call_id for o in obs}), 3)
        gw.close()

    def test_skill_reference_no_path_traversal(self):
        gw, _ = gateway()
        good = gw.run_batch([{"tool": "read_skill_reference",
                              "arguments": {"skill": "regulatory-check", "file": "references/fda-pathways.md"}}])[0]
        bad = gw.run_batch([{"tool": "read_skill_reference",
                             "arguments": {"skill": "regulatory-check", "file": "../../medscout/config.py"}}])[0]
        self.assertTrue(good.ok)
        self.assertFalse(bad.ok)
        gw.close()


if __name__ == "__main__":
    unittest.main()
