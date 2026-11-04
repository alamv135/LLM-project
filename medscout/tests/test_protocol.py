"""Parser checks without a model (Lec 4 Task B: 'all must pass before integration')."""

import json
import unittest

from medscout.protocol import ProtocolError, parse_decision

TOOLS = {"fda_510k_search", "pubmed_search"}


def ok(obj):
    return parse_decision(json.dumps(obj) if not isinstance(obj, str) else obj, TOOLS, 3)


class TestDecisionParser(unittest.TestCase):
    def test_accept_tool_calls(self):
        d = ok({"action": "tool_calls", "calls": [{"tool": "fda_510k_search", "arguments": {"applicant": "X"}}]})
        self.assertEqual(d["action"], "tool_calls")

    def test_accept_finish(self):
        d = ok({"action": "finish", "assessments": [{"claim_id": "C1", "verdict": "unresolved", "evidence_ids": [],
                                                      "rationale": "source unavailable today", "open_questions": ["?"]}]})
        self.assertEqual(d["assessments"][0]["verdict"], "unresolved")

    def test_accept_fenced_json_with_prose(self):
        d = ok('Here you go:\n```json\n{"action":"ask_user","question":"Same company?"}\n```')
        self.assertEqual(d["action"], "ask_user")

    def test_reject_not_json(self):
        with self.assertRaises(ProtocolError):
            ok("I will search the FDA database now.")

    def test_reject_unknown_action(self):
        with self.assertRaises(ProtocolError):
            ok({"action": "refund"})

    def test_reject_unknown_tool(self):
        with self.assertRaises(ProtocolError):
            ok({"action": "tool_calls", "calls": [{"tool": "delete_db", "arguments": {}}]})

    def test_reject_unknown_verdict(self):
        with self.assertRaises(ProtocolError):
            ok({"action": "finish", "assessments": [{"claim_id": "C1", "verdict": "true", "evidence_ids": [],
                                                      "rationale": "looks fine to me"}]})

    def test_reject_bad_evidence_id_and_extra_keys(self):
        with self.assertRaises(ProtocolError):
            ok({"action": "finish", "assessments": [{"claim_id": "C1", "verdict": "unresolved",
                                                      "evidence_ids": ["K251234"], "rationale": "cites a raw id"}]})
        with self.assertRaises(ProtocolError):
            ok({"action": "ask_user", "question": "Same company?", "sql": "DROP TABLE"})

    def test_reject_too_many_calls(self):
        call = {"tool": "pubmed_search", "arguments": {"query": "x"}}
        with self.assertRaises(ProtocolError):
            ok({"action": "tool_calls", "calls": [call] * 4})


if __name__ == "__main__":
    unittest.main()
