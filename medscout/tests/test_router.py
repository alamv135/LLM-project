"""Model list, providers, router, cost limits, privacy and the SQLite database.

Runs against tests/fake_ollama.py (local models) and tests/fake_openrouter.py (Claude, DeepSeek,
Gemini through an OpenAI-compatible API). No network, no real keys.
"""

import http.client
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from medscout.ask import web
from medscout.config import ROOT
from medscout.localdb import DatabaseError, LocalDB, as_markdown
from medscout.models import Registry, Router
from medscout.models.providers import OpenAIProvider, to_openai
from medscout.models.router import BudgetExceeded, Needs, PrivacyBlocked, heuristic_difficulty
from medscout.web.server import make_server
from medscout.workflow import Workflow

from . import fake_ollama, fake_openrouter
from .helpers import new_state, temp_settings


def make_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE deals (id INTEGER PRIMARY KEY, company TEXT, stage TEXT, amount_k INTEGER, notes TEXT);
        INSERT INTO deals VALUES (1, 'Nordleaf Cardio', 'screening', 2500, 'ECG patch'),
                                 (2, 'Quillon', 'passed', 0, 'no FDA record'), (3, 'Acme Med', 'diligence', 4000, NULL);
        CREATE TABLE meetings (id INTEGER PRIMARY KEY, deal_id INTEGER REFERENCES deals(id), held TEXT, summary TEXT);
        INSERT INTO meetings VALUES (1, 1, '2026-10-01', 'Founder call');
    """)
    conn.commit()
    conn.close()
    return path


class RouterBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ollama = fake_ollama.start(0)
        cls.orouter = fake_openrouter.start()
        cls.tmp = Path(tempfile.mkdtemp(prefix="medscout-router-"))
        cfg = json.loads((ROOT / "config" / "models.json").read_text())
        cfg["providers"]["ollama"] = {"kind": "ollama", "base_url": "http://127.0.0.1:%d" % cls.ollama.server_address[1]}
        cfg["providers"]["openrouter"]["base_url"] = "http://127.0.0.1:%d/api/v1" % cls.orouter.server_address[1]
        cfg["providers"]["deepseek"]["base_url"] = "http://127.0.0.1:%d/deepseek" % cls.orouter.server_address[1]
        cls.models_path = cls.tmp / "models.json"
        cls.models_path.write_text(json.dumps(cfg))
        cls.db_path = make_db(cls.tmp / "deals.sqlite")
        web.DDG_HTML_URL = "http://127.0.0.1:%d/html/" % cls.ollama.server_address[1]
        web.ALLOW_PRIVATE = True

    @classmethod
    def tearDownClass(cls):
        web.ALLOW_PRIVATE = False
        cls.ollama.shutdown()
        cls.orouter.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("MEDSCOUT_MODELS", "OPENROUTER_API_KEY", "OLLAMA_HOST", "DEEPSEEK_API_KEY")}
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ["MEDSCOUT_MODELS"] = str(self.models_path)
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        os.environ.pop("OLLAMA_HOST", None)
        fake_ollama.FakeOllama.requests.clear()
        fake_ollama.FakeOllama.delay = 0.0
        fake_openrouter.FakeOpenRouter.requests.clear()
        self.reg = Registry(self.models_path, self.tmp)
        self.router = Router(self.reg)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def cloud_calls(self):
        return [r for r in fake_openrouter.FakeOpenRouter.requests if r.get("model")]

    def local_chats(self):
        return [r for r in fake_ollama.FakeOllama.requests if "messages" in r]


class TestRegistryAndProviders(RouterBase):
    def test_catalog_refresh_updates_prices_and_abilities(self):
        self.assertEqual(self.reg.get("deepseek").price_in, 0.15)            # from models.json
        info = self.reg.refresh_catalog(force=True)
        self.assertIn("3 of 3 cloud models found", info)
        ds = self.reg.get("deepseek")
        self.assertAlmostEqual(ds.price_in, 0.2)                              # 0.0000002 per token -> $0.20 / 1M
        self.assertAlmostEqual(ds.price_out, 0.8)
        self.assertTrue(ds.json_schema)
        self.assertFalse(ds.vision)
        self.assertTrue(self.reg.get("gemini").vision)
        self.assertFalse(self.reg.get("claude").json_schema)
        self.assertAlmostEqual(ds.cost(1_000_000, 1_000_000), 1.0)
        # other names work too
        self.assertTrue(self.reg.get("llama3.2:3b").local)
        self.assertEqual(self.reg.get("qwen3.5:9b").alias, "qwen-local")
        self.assertFalse(self.reg.get("openrouter:google/gemini-3.8-flash").local)
        with self.assertRaises(KeyError):
            self.reg.get("nonsense")

    def test_local_status_and_keys(self):
        self.assertIn("2 of 2 local models downloaded", self.reg.refresh_local())
        self.assertTrue(self.reg.available(self.reg.get("qwen-local"))[0])
        os.environ.pop("OPENROUTER_API_KEY")
        ok, why = self.reg.available(self.reg.get("claude"))
        self.assertFalse(ok)
        self.assertIn("no API key", why)

    def test_message_conversion(self):
        msgs = to_openai([
            {"role": "user", "content": "look", "images": ["iVBORw0KGgo="]},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "web_search", "arguments": {"query": "x"}}}]},
            {"role": "tool", "tool_name": "web_search", "tool_call_id": "c1", "content": "[1] result"},
        ])
        self.assertEqual(msgs[0]["content"][1]["image_url"]["url"], "data:image/png;base64,iVBORw0KGgo=")
        self.assertEqual(msgs[1]["tool_calls"][0]["function"]["arguments"], '{"query": "x"}')
        self.assertIsNone(msgs[1]["content"])
        self.assertEqual(msgs[2], {"role": "tool", "tool_call_id": "c1", "content": "[1] result"})

    def test_openai_provider_complete_and_stream(self):
        p = self.router.provider(self.reg.get("deepseek"))
        self.assertIsInstance(p, OpenAIProvider)
        res = p.complete("deepseek/deepseek-v4.1-flash", [{"role": "user", "content": "PHASE: plan\nCLAIMS_JSON:\n[]\nEND_CLAIMS"}],
                         {"type": "object"}, json_schema=True)
        self.assertEqual((res.tokens_in, res.tokens_out, res.cost_usd), (300, 40, fake_openrouter.COST))
        sent = self.cloud_calls()[-1]
        self.assertEqual(sent["response_format"]["type"], "json_schema")
        self.assertEqual(sent["usage"], {"include": True})
        self.assertEqual(sent["_auth"], "Bearer test-key")
        chunks = list(p.stream({"model": "deepseek/deepseek-v4.1-flash", "messages": [{"role": "user", "content": "search it"}],
                                "tools": [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]}))
        calls = [c for c in chunks if c["message"].get("tool_calls")]
        self.assertEqual(calls[0]["message"]["tool_calls"][0],
                         {"id": "call_abc", "function": {"name": "web_search", "arguments": {"query": "ollama release"}}})
        self.assertEqual(chunks[-1]["cost"], fake_openrouter.COST)
        self.assertTrue(chunks[-1]["done"])


class TestDeepSeekDirect(RouterBase):
    def test_used_when_its_key_is_set(self):
        self.assertEqual(self.router.for_run("plan", Needs()).model.alias, "deepseek")        # no key: OpenRouter
        os.environ["DEEPSEEK_API_KEY"] = "ds-key"
        d = self.router.for_run("plan", Needs())
        self.assertEqual(d.model.alias, "deepseek-direct")
        m = d.model
        res = self.router.provider(m).complete(m.id, [{"role": "user", "content": "PHASE: plan\nCLAIMS_JSON:\n[]\nEND_CLAIMS"}],
                                               {"type": "object"}, json_schema=m.json_schema)
        sent = self.cloud_calls()[-1]
        self.assertEqual((sent["_path"], sent["_auth"]), ("/deepseek/chat/completions", "Bearer ds-key"))
        self.assertEqual(sent["response_format"], {"type": "json_object"})   # DeepSeek: JSON mode, no schema
        self.assertNotIn("usage", sent)                                       # OpenRouter-only option
        self.assertIsNone(res.cost_usd)                                       # DeepSeek reports no cost ...
        self.assertAlmostEqual(m.cost(res.tokens_in, res.tokens_out), (300 * 0.14 + 40 * 0.28) / 1e6)

    def test_run_and_ask_through_direct_api(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds-key"
        s = temp_settings(provider="router", model="auto")
        st, cdir = new_state(focus=["regulatory"])
        Workflow(s, st, case_dir=cdir, echo=False).run()
        self.assertEqual(st.phase, "done")
        direct = [r for r in self.cloud_calls() if r["_path"].startswith("/deepseek/")]
        self.assertTrue(direct)
        self.assertFalse([r for r in self.cloud_calls() if r["_path"].startswith("/api/v1/")])
        self.assertAlmostEqual(st.counters.cost_usd, len(direct) * (300 * 0.14 + 40 * 0.28) / 1e6, places=5)
        # streamed chat with the direct API
        chunks = list(self.router.provider(self.reg.get("deepseek-direct")).stream(
            {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hello"}]}))
        self.assertIn("deepseek-v4-flash", "".join(c["message"].get("content", "") for c in chunks))
        self.assertIsNone(chunks[-1]["cost"])

    def test_deepseek_quirks(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds-key"
        m = self.reg.get("deepseek-direct")
        p = self.router.provider(m)
        msgs = [{"role": "system", "content": "Return one JSON object."},
                {"role": "user", "content": "PHASE: plan\nCLAIMS_JSON:\n[]\nEND_CLAIMS"}]
        res = p.complete(m.id, msgs, {"type": "object"})
        self.assertTrue(res.text.startswith("{") and res.text.endswith("}"))      # padding removed
        self.assertEqual(res.cached_tokens, 200)
        self.assertAlmostEqual(m.cost(res.tokens_in, res.tokens_out, res.cached_tokens), (300 * 0.14 + 40 * 0.28) / 1e6)
        m.price_cache_in = 0.014                                                 # once you set a cache price
        self.assertAlmostEqual(m.cost(300, 40, 200), (100 * 0.14 + 200 * 0.014 + 40 * 0.28) / 1e6)
        # empty content once: asked again automatically; twice: a clear error
        fake_openrouter.FakeOpenRouter.deepseek_empty = 1
        self.assertTrue(p.complete(m.id, msgs, {"type": "object"}).text.startswith("{"))
        fake_openrouter.FakeOpenRouter.deepseek_empty = 2
        with self.assertRaisesRegex(Exception, "empty content"):
            p.complete(m.id, msgs, {"type": "object"})
        fake_openrouter.FakeOpenRouter.deepseek_empty = 0
        with self.assertRaisesRegex(Exception, "cut off"):
            p.complete(m.id, msgs, {"type": "object"}, max_tokens=5)
        with self.assertRaisesRegex(Exception, "must contain the word 'json'"):
            p.complete(m.id, [{"role": "user", "content": "plan please"}], {"type": "object"})

    def test_deepseek_router_falls_back_after_empty_answers(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds-key"
        from medscout.models.routed import RoutedLLM
        llm = RoutedLLM(self.router, "auto")
        fake_openrouter.FakeOpenRouter.deepseek_empty = 2
        res = llm.complete([{"role": "system", "content": "JSON only"}, {"role": "user", "content": "PHASE: plan\nCLAIMS_JSON:\n[]\nEND_CLAIMS"}],
                           {"type": "object"}, purpose="plan")
        self.assertEqual(res.model, "deepseek")                                   # next in the list: via OpenRouter
        self.assertIn("fallback", res.route)

    def test_deepseek_thinking_with_tools_in_ask(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds-key"
        p = self.router.provider(self.reg.get("deepseek-direct"))
        tool_def = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
        first = list(p.stream({"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "search it"}], "tools": tool_def}))
        thinking = "".join(c["message"].get("thinking", "") for c in first)
        self.assertEqual(thinking, "DeepSeek thinks first. ")
        call = next(c for c in first if c["message"].get("tool_calls"))["message"]["tool_calls"][0]
        history = [{"role": "user", "content": "search it"},
                   {"role": "assistant", "content": "", "thinking": thinking, "tool_calls": [call]},
                   {"role": "tool", "tool_name": "web_search", "tool_call_id": call["id"], "content": "[1] result"}]
        second = list(p.stream({"model": "deepseek-v4-flash", "messages": history, "tools": tool_def}))
        self.assertIn("[1]", "".join(c["message"].get("content", "") for c in second))
        sent = self.cloud_calls()[-1]["messages"][1]
        self.assertEqual(sent["reasoning_content"], "DeepSeek thinks first. ")    # sent back, as DeepSeek requires
        # OpenRouter never receives that field
        list(self.router.provider(self.reg.get("deepseek")).stream({"model": "deepseek/deepseek-v4.1-flash", "messages": history, "tools": tool_def}))
        self.assertNotIn("reasoning_content", self.cloud_calls()[-1]["messages"][1])

    def test_model_id_check_and_names(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds-key"
        self.reg.models["deepseek-direct"].id = "deepseek-chat"                # an old, retired name
        info = self.reg.check_direct_providers()
        self.assertIn("deepseek: 2 models offered", info)
        self.assertIn("not offered by deepseek", self.reg.get("deepseek-direct").notes[0])
        m = self.reg.get("deepseek:deepseek-v4-pro")
        self.assertEqual((m.provider, m.id, m.local), ("deepseek", "deepseek-v4-pro", False))
        self.assertEqual(self.reg.get("anthropic/claude-sonnet-5.5").provider, "openrouter")
        self.reg.refresh_catalog(force=True)
        self.assertFalse(self.reg.get("deepseek-direct").notes[1:])            # not matched against OpenRouter


class TestRouterDecisions(RouterBase):
    def test_privacy_ability_budget_and_keys(self):
        names = ["claude", "deepseek", "qwen-local"]
        self.assertEqual(self.router.pick(names, Needs(), "t").model.alias, "claude")
        d = self.router.pick(names, Needs(private=True), "t")
        self.assertEqual(d.model.alias, "qwen-local")
        self.assertIn("private material stays on local models", d.reason)
        self.assertEqual(self.router.pick(names, Needs(private=True, allow_cloud=True), "t").model.alias, "claude")
        self.assertEqual(self.router.pick(["deepseek", "gemini"], Needs(vision=True), "t").model.alias, "gemini")
        d = self.router.pick(names, Needs(tokens=10000, spent=0.0, limit=0.02), "t")   # claude ~$0.04 > limit
        self.assertEqual(d.model.alias, "deepseek")
        self.assertIn("cost limit", d.reason)
        os.environ.pop("OPENROUTER_API_KEY")
        self.assertEqual(self.router.pick(names, Needs(), "t").model.alias, "qwen-local")

    def test_single_model_explains_problems(self):
        with self.assertRaises(PrivacyBlocked):
            self.router.single("claude", Needs(private=True))
        with self.assertRaises(BudgetExceeded):
            self.router.single("claude", Needs(tokens=100000, limit=0.01))
        self.assertEqual(self.router.single("qwen-local", Needs(private=True)).reason, "chosen by you")

    def test_ask_routing(self):
        cls = lambda q: ("hard", "test rating")       # noqa: E731
        self.assertEqual(heuristic_difficulty("hi")[0], "easy")
        self.assertEqual(self.router.for_ask("thanks!", Needs(), cls).model.alias, "qwen-local")
        d = self.router.for_ask("Compare these three regulatory strategies for our device", Needs(), cls)
        self.assertEqual((d.model.alias, d.difficulty), ("deepseek", "hard"))
        self.assertEqual(self.router.for_ask("please answer this @claude", Needs()).model.alias, "claude")
        d = self.router.for_ask("anything", Needs(), cls, escalate_from="qwen-local")
        self.assertEqual(d.model.tier, 1)
        d = self.router.for_ask("anything", Needs(), cls, escalate_from="deepseek")
        self.assertEqual(d.model.alias, "claude")
        # the real classifier is the free local model (fake Ollama rates "compare" as hard)
        diff, how = self.router.classifier()("Compare A and B in depth please")
        self.assertEqual(diff, "hard")
        self.assertIn("rated by qwen-local", how)

    def test_run_routing_and_escalation(self):
        self.assertEqual(self.router.for_run("research", Needs()).model.alias, "qwen-local")
        self.assertEqual(self.router.for_run("plan", Needs()).model.alias, "deepseek")
        d = self.router.for_run("research", Needs(), failures=1)
        self.assertEqual(d.model.tier, 1)
        self.assertIn("moved up after 1 failure", d.reason)
        self.assertEqual(self.router.for_run("plan", Needs(private=True)).model.alias, "qwen-local")


class TestRunsThroughRouter(RouterBase):
    def run_case(self, **over):
        s = temp_settings(provider="router", model="auto", **over)
        st, cdir = new_state(focus=["regulatory"])
        Workflow(s, st, case_dir=cdir, echo=False).run()
        events = [json.loads(x) for x in (s.runs_dir / st.run_id / "trace.jsonl").read_text().splitlines()]
        return st, [e for e in events if e.get("kind") == "model_call"]

    def test_auto_run_uses_cloud_for_planning_and_local_for_research(self):
        st, calls = self.run_case()
        self.assertEqual(st.phase, "done")
        by_purpose = {}
        for c in calls:
            by_purpose.setdefault(c["purpose"], set()).add(c["model"])
        self.assertEqual(by_purpose["extract_claims"], {"deepseek"})
        self.assertEqual(by_purpose["plan"], {"deepseek"})
        self.assertEqual(by_purpose["research"], {"qwen-local"})
        self.assertTrue(all(c["route"] for c in calls))
        cloud = sum(1 for c in calls if c["model"] == "deepseek")
        self.assertAlmostEqual(st.counters.cost_usd, cloud * fake_openrouter.COST, places=6)

    def test_cost_limit_keeps_the_run_local(self):
        s = temp_settings(provider="router", model="auto")
        s.limits.max_cost_usd = 0.0001
        st, cdir = new_state(focus=["regulatory"])
        Workflow(s, st, case_dir=cdir, echo=False).run()
        self.assertEqual(st.phase, "done")
        self.assertEqual(st.counters.cost_usd, 0)
        self.assertFalse(self.cloud_calls())

    def test_private_documents_stay_local_and_single_cloud_model_is_refused(self):
        s = temp_settings(provider="router", model="auto")
        st, cdir = new_state(focus=["regulatory"])
        Workflow(s, st, case_dir=None, echo=False).run()          # no case = the user's own documents
        self.assertFalse(self.cloud_calls())
        self.assertTrue(st.config["private"])
        s2 = temp_settings(provider="router", model="claude")
        st2, _ = new_state()
        with self.assertRaises(PrivacyBlocked):
            Workflow(s2, st2, case_dir=None, echo=False)
        s2.allow_cloud = True
        Workflow(s2, new_state()[0], case_dir=None, echo=False)   # allowed: no error

    def test_run_with_database_tools(self):
        os.environ["MEDSCOUT_DATA_DB"] = str(self.db_path)
        try:
            s = temp_settings(provider="mock", use_database=True)
            st, cdir = new_state(focus=["regulatory"])
            wf = Workflow(s, st, case_dir=cdir, echo=False)
            self.assertIn("db_query", wf.specs)
            self.assertTrue(st.config["private"])
            obs = wf.gateway.run_batch([{"tool": "db_query", "arguments": {"sql": "SELECT company FROM deals ORDER BY id"}}])[0]
            self.assertTrue(obs.ok, obs.error)
            ev = st.evidence[obs.evidence_ids[0]]
            self.assertEqual((ev.source, ev.independent), ("Local database", False))
            self.assertEqual(ev.fields["company"], "Nordleaf Cardio")
            bad = wf.gateway.run_batch([{"tool": "db_query", "arguments": {"sql": "DELETE FROM deals"}}])[0]
            self.assertFalse(bad.ok)
            wf.gateway.close()
        finally:
            os.environ.pop("MEDSCOUT_DATA_DB", None)


class TestLocalDB(RouterBase):
    def test_read_only_and_limits(self):
        db = LocalDB(self.db_path)
        self.assertEqual([t["name"] for t in db.tables()], ["deals", "meetings"])
        self.assertEqual(db.describe("deals")["rows"], 3)
        self.assertEqual(db.describe("meetings")["foreign_keys"][0]["references"], "deals.id")
        r = db.query("SELECT stage, count(*) n FROM deals GROUP BY stage ORDER BY stage")
        self.assertEqual(r["rows"], [["diligence", 1], ["passed", 1], ["screening", 1]])
        self.assertIn("| diligence | 1 |", as_markdown(r))
        for sql in ("DELETE FROM deals", "DROP TABLE deals", "UPDATE deals SET stage='x'",
                    "INSERT INTO deals(company) VALUES ('x')", "ATTACH DATABASE '/tmp/x.db' AS x",
                    "PRAGMA writable_schema=1", "SELECT 1; DROP TABLE deals",
                    "WITH x AS (SELECT 1) DELETE FROM deals"):
            with self.assertRaises(DatabaseError, msg=sql):
                db.query(sql)
        self.assertEqual(db.query("SELECT count(*) FROM deals")["rows"], [[3]])     # nothing changed
        big = db.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 1000) SELECT x FROM c")
        self.assertEqual((big["row_count"], big["truncated"]), (200, True))
        db.timeout_s = 0.2
        with self.assertRaises(DatabaseError) as ctx:
            db.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT count(*) FROM c")
        self.assertIn("stopped", str(ctx.exception))
        db.close()
        with self.assertRaises(DatabaseError):
            LocalDB(self.tmp / "missing.db")


class TestAskThroughRouter(RouterBase):
    def setUp(self):
        super().setUp()
        self.ms = temp_settings()
        self.server = make_server("127.0.0.1", 0, self.ms)
        self.port = self.server.server_address[1]
        self.ask = self.server.app.ask
        self.ask.settings.update({"ollama_url": "http://127.0.0.1:%d" % self.ollama.server_address[1]})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        conn.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None), headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def send(self, **req):
        req.setdefault("medtech", False)
        _, data = self.request("POST", "/api/ask/send", req)
        return [json.loads(x) for x in data.decode().splitlines() if x.strip()]

    def test_easy_question_stays_local_and_free(self):
        events = self.send(text="hello there", web_search=False)
        route = next(e for e in events if e["type"] == "route")
        self.assertEqual((route["model"], route["local"]), ("qwen-local", True))
        done = events[-1]["message"]
        self.assertEqual(done["model"], "qwen-local")
        self.assertEqual(done["stats"]["cost_usd"], 0)
        self.assertFalse(self.cloud_calls())

    def test_hard_question_goes_to_cloud_with_streaming_and_cost(self):
        events = self.send(text="Compare the 510(k) and De Novo routes for this device", web_search=False)
        route = next(e for e in events if e["type"] == "route")
        self.assertEqual((route["model"], route["difficulty"]), ("deepseek", "hard"))
        self.assertIn("rated by qwen-local", route["reason"])
        streamed = "".join(e["text"] for e in events if e["type"] == "text")
        self.assertEqual(streamed, "Cloud model deepseek/deepseek-v4.1-flash says hello.")
        done = events[-1]["message"]
        self.assertEqual(done["stats"]["cost_usd"], fake_openrouter.COST)
        self.assertEqual(done["model_label"], "DeepSeek V4.1 Flash")

    def test_cloud_tool_calls_round_trip(self):
        self.ask.settings.update({"model": "deepseek"})
        events = self.send(text="search for the latest Ollama news", web_search=True)
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertTrue(end["ok"])
        second = self.cloud_calls()[-1]["messages"]
        self.assertEqual(second[-2]["tool_calls"][0]["id"], "call_abc")
        self.assertEqual(second[-1]["tool_call_id"], "call_abc")
        self.assertIn("[1]", events[-1]["message"]["content"])
        self.assertAlmostEqual(events[-1]["message"]["stats"]["cost_usd"], 2 * fake_openrouter.COST)

    def test_deepseek_direct_tool_round_in_ask(self):
        os.environ["DEEPSEEK_API_KEY"] = "ds-key"
        self.ask.settings.update({"model": "deepseek-direct"})
        events = self.send(text="search for the latest Ollama news", web_search=True)
        self.assertIsNone(events[-1]["message"]["error"])
        self.assertEqual(next(e for e in events if e["type"] == "tool_end")["ok"], True)
        self.assertIn("DeepSeek thinks first.", "".join(e["text"] for e in events if e["type"] == "thinking"))
        self.assertIn("[1]", events[-1]["message"]["content"])
        sent = self.cloud_calls()[-1]
        self.assertEqual(sent["_path"], "/deepseek/chat/completions")
        self.assertIn("reasoning_content", next(m for m in sent["messages"] if m.get("tool_calls")))
        self.assertAlmostEqual(events[-1]["message"]["stats"]["cost_usd"], 2 * (300 * 0.14 + 40 * 0.28) / 1e6, places=6)

    def test_private_files_stay_local_unless_allowed(self):
        _, raw = self.request("POST", "/api/ask/upload", raw=b"Confidential term sheet", headers={"X-Filename": "terms.txt"})
        fid = json.loads(raw)["id"]
        events = self.send(text="Compare this term sheet with market norms", attachments=[fid], web_search=False)
        route = next(e for e in events if e["type"] == "route")
        self.assertEqual(route["model"], "qwen-local")
        self.assertIn("stay on this computer", route["reason"])
        self.assertFalse(self.cloud_calls())
        chat_id = events[0]["chat"]["id"]
        events = self.send(chat_id=chat_id, text="Compare it again in depth", web_search=False, allow_cloud=True)
        self.assertEqual(next(e for e in events if e["type"] == "route")["model"], "deepseek")
        self.assertIn("Confidential term sheet", json.dumps(self.cloud_calls()[-1]["messages"]))
        # a single cloud model is refused for private content without permission
        self.ask.settings.update({"model": "claude"})
        events = self.send(chat_id=chat_id, text="and now?", web_search=False, allow_cloud=False)
        self.assertIn("cloud model", events[-1]["message"]["error"])

    def test_cost_limit_per_answer(self):
        self.ask.settings.update({"model": "claude", "max_cost_per_answer": 0.0001})
        events = self.send(text="Compare everything", web_search=False)
        self.assertIn("cost limit", events[-1]["message"]["error"])
        self.assertFalse(self.cloud_calls())
        self.ask.settings.update({"model": "auto"})
        events = self.send(text="Compare everything in depth", web_search=False)
        route = next(e for e in events if e["type"] == "route")
        self.assertEqual(route["model"], "qwen-local")
        self.assertIn("cost limit", route["reason"])

    def test_try_a_stronger_model(self):
        events = self.send(text="hi", web_search=False)
        chat_id = events[0]["chat"]["id"]
        events = self.send(chat_id=chat_id, escalate_from="qwen-local", web_search=False)
        route = next(e for e in events if e["type"] == "route")
        self.assertEqual(route["tier"], 1)
        _, raw = self.request("GET", "/api/ask/chats/" + chat_id)
        self.assertEqual([m["role"] for m in json.loads(raw)["messages"]], ["user", "assistant"])

    def test_missing_key_falls_back_or_explains(self):
        os.environ.pop("OPENROUTER_API_KEY")
        events = self.send(text="Compare the pathways in depth", web_search=False)
        self.assertEqual(next(e for e in events if e["type"] == "route")["model"], "qwen-local")
        self.ask.settings.update({"model": "claude"})
        events = self.send(text="hello", web_search=False)
        self.assertIn("no API key", events[-1]["message"]["error"])

    def test_database_in_ask(self):
        status, raw = self.request("POST", "/api/database", {"database_path": str(self.db_path), "database_notes": "screened deals"})
        self.assertEqual(status, 200, raw)
        info = json.loads(raw)
        self.assertEqual([t["name"] for t in info["tables"]], ["deals", "meetings"])
        self.assertEqual(self.request("POST", "/api/database", {"database_path": str(self.tmp / "nope.db")})[0], 400)
        events = self.send(text="how many deals are in my pipeline?", web_search=False, database=True)
        route = next(e for e in events if e["type"] == "route")
        self.assertTrue(route["local"])                                     # database data is private
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertTrue(end["ok"], end)
        self.assertEqual(end["label"], "Queried your database (1 row)")
        first = self.local_chats()[-2]
        self.assertIn("deals (3 rows)", first["messages"][0]["content"])  # schema overview in the prompt
        self.assertIn("screened deals", first["messages"][0]["content"])
        self.assertIn("Your database has 3 deals", events[-1]["message"]["content"])
        events = self.send(text="drop the deals table", web_search=False, database=True)
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertFalse(end["ok"])
        self.assertEqual(LocalDB(self.db_path).query("SELECT count(*) FROM deals")["rows"], [[3]])

    def test_console_model_endpoints(self):
        status, raw = self.request("GET", "/api/models?refresh=1")
        data = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual({m["alias"] for m in data["models"]}, {"qwen-local", "gemma-local", "deepseek-direct", "deepseek", "gemini", "claude"})
        self.assertTrue(data["openrouter_key_set"])
        _, raw = self.request("POST", "/api/models/test", {"model": "deepseek"})
        self.assertTrue(json.loads(raw)["ok"])
        _, raw = self.request("POST", "/api/models/route", {"question": "Compare A and B carefully", "private": True})
        r = json.loads(raw)
        self.assertEqual(r["model"], "qwen-local")
        _, raw = self.request("GET", "/api/config")
        self.assertIn("claude", [m["alias"] for m in json.loads(raw)["models"]])
        # a broken models.json is refused by the editor
        status, raw = self.request("PUT", "/api/file", {"path": "config/models.json",
                                                         "content": '{"providers": {}, "models": {"x": {"provider": "nope", "id": "y"}}}'})
        self.assertEqual(status, 400)
        self.assertIn("unknown provider", json.loads(raw)["error"])

    def test_start_run_refuses_cloud_model_for_private_documents(self):
        import base64
        body = {"company": "Acme", "uploads": [{"name": "deck.md", "data_b64": base64.b64encode(b"# Acme\nFDA cleared.").decode()}],
                "settings": {"provider": "router", "model": "claude", "backend": "mirror"}}
        status, raw = self.request("POST", "/api/runs", body)
        self.assertEqual(status, 400)
        self.assertIn("private", json.loads(raw)["error"])


if __name__ == "__main__":
    unittest.main()
