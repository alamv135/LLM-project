"""MCP transport: discovery via tools/list and invocation via tools/call (skipped without fastmcp)."""

import importlib.util
import unittest

from medscout.workflow import Workflow

from .helpers import new_state, temp_settings


@unittest.skipUnless(importlib.util.find_spec("fastmcp"), "fastmcp not installed")
class TestMCPTransport(unittest.TestCase):
    def test_workflow_over_mcp(self):
        s = temp_settings(tool_transport="mcp")
        st, cdir = new_state(focus=["regulatory"])
        wf = Workflow(s, st, case_dir=cdir, echo=False)
        self.assertIn("fda_510k_search", wf.gateway.mcp_tools)
        self.assertNotIn("search_company_docs", wf.gateway.mcp_tools)   # local tools stay local
        wf.run()
        mcp_calls = [e for e in wf.tracer.events if e["kind"] == "tool_call" and e["transport"] == "mcp"]
        self.assertTrue(mcp_calls and all(e["ok"] for e in mcp_calls))
        c = next(c for c in st.claims if "FDA approved" in c.text)
        self.assertEqual(st.findings[c.claim_id].verdict, "partially_supported")


if __name__ == "__main__":
    unittest.main()
