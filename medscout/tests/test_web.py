"""Web API: a controlled run driven over HTTP, plus the guards (single run, file allowlist, origin check)."""

import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from medscout.web.server import make_server

from .helpers import temp_settings


class TestWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = make_server("127.0.0.1", 0, temp_settings())
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def call(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_idle(self, rid, answer, timeout=30):
        seen, t_end = [], time.time() + timeout
        while time.time() < t_end:
            _, v = self.call("GET", f"/api/runs/{rid}")
            if v["status"] in ("done", "stopped", "error"):
                return v, seen
            p = v["control"]["pending"]
            if p:
                seen.append(p["kind"])
                self.call("POST", f"/api/runs/{rid}/respond", {"request_id": p["request_id"],
                                                               "response": answer(p["kind"], p["payload"])})
            time.sleep(0.02)
        self.fail("run did not finish")

    def test_1_controlled_run_over_http(self):
        st, cfg = self.call("GET", "/api/config")
        self.assertEqual(st, 200)
        self.assertIn("nordleaf", [c["case"] for c in cfg["cases"]])
        st, r = self.call("POST", "/api/runs", {"case": "nordleaf", "focus": ["regulatory"],
                                                "control": {"review_plan": True, "approve_tools": True,
                                                            "ask_reviewer": True},
                                                "limits": {"max_steps_per_task": 6}})
        self.assertEqual(st, 201)
        rid = r["run_id"]
        self.assertEqual(self.call("POST", "/api/runs", {"case": "nordleaf"})[0], 409)   # one run at a time
        self.call("POST", f"/api/runs/{rid}/note", {"text": "Use the K-number if one is stated."})

        def answer(kind, payload):
            if kind == "review_plan":
                return {"tasks": [{**t, "keep": i == 0} for i, t in enumerate(payload["tasks"])]}  # keep only T1
            if kind == "approve_tools":
                return {"decision": "approve"}
            if kind == "ask_user":
                return {"answer": "yes"}
            return {"continue": True}

        v, seen = self.wait_idle(rid, answer)
        self.assertEqual(v["status"], "done")
        self.assertIn("review_plan", seen)
        self.assertIn("approve_tools", seen)
        self.assertEqual(len(v["tasks"]), 1)
        removed = [f for f in v["findings"].values() if f["task_id"] == "removed"]
        self.assertEqual(len(removed), 3)
        st, ev = self.call("GET", f"/api/runs/{rid}/events?after=0")
        kinds = {e.get("event") for e in ev["events"] if e["kind"] == "control"}
        self.assertTrue({"waiting", "answered", "plan_reviewed", "note_delivered"} <= kinds)
        st, b = self.call("GET", f"/api/runs/{rid}/brief")
        self.assertEqual(st, 200)
        self.assertIn("Investment Readiness Brief", b["markdown"])
        st, runs = self.call("GET", "/api/runs")
        self.assertIn(rid, [x["run_id"] for x in runs["runs"]])

    def test_2_stop_and_resume_over_http(self):
        st, r = self.call("POST", "/api/runs", {"case": "nordleaf", "focus": ["regulatory"],
                                                "control": {"step_mode": True, "ask_reviewer": False}})
        rid = r["run_id"]
        for _ in range(200):
            _, v = self.call("GET", f"/api/runs/{rid}")
            if v["control"]["pending"]:
                break
            time.sleep(0.02)
        self.call("POST", f"/api/runs/{rid}/control", {"action": "stop"})
        for _ in range(200):
            _, v = self.call("GET", f"/api/runs/{rid}")
            if v["status"] == "stopped":
                break
            time.sleep(0.02)
        self.assertEqual(v["status"], "stopped")
        st, _ = self.call("POST", f"/api/runs/{rid}/resume", {"control": {"step_mode": False, "ask_reviewer": False}})
        self.assertEqual(st, 200)
        v, _ = self.wait_idle(rid, lambda k, p: {"continue": True})
        self.assertEqual(v["status"], "done")

    def test_3_guards_and_tools(self):
        self.assertEqual(self.call("GET", "/api/file?path=medscout/config.py")[0], 403)
        self.assertEqual(self.call("PUT", "/api/file", {"path": "eval/cases.json", "content": "{not json"})[0], 400)
        self.assertEqual(self.call("POST", "/api/connection", {}, {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/runs", {"company": "X"})[0], 400)          # no documents
        self.assertEqual(self.call("POST", "/api/runs", {"case": "nordleaf", "faults": "rm -rf"})[0], 400)
        st, tc = self.call("POST", "/api/tool-check", {"tool": "fda_510k_search", "case": "nordleaf",
                                                       "arguments": {"product_code": "ZXA"},
                                                       "faults": "fda_510k_search=timeout*1"})
        self.assertEqual(st, 200)
        self.assertEqual(tc["observation"]["total"], 3)
        self.assertEqual(tc["attempts"], 2)                                                  # retried once
        st, conn = self.call("POST", "/api/connection", {"openai_api_key": "sk-test"})
        self.assertTrue(conn["openai_api_key"]["set"])
        self.assertNotIn("value", conn["openai_api_key"])                                   # never echoed back
        self.call("POST", "/api/connection", {"clear": ["openai_api_key"]})
        st, t = self.call("POST", "/api/connection/test", {"settings": {"provider": "mock"}})
        self.assertTrue(t["ok"])
        with urllib.request.urlopen(self.base + "/") as r:                                   # the UI itself
            self.assertIn("text/html", r.headers["Content-Type"])
            self.assertIn(b"/app.js", r.read())


if __name__ == "__main__":
    unittest.main()
