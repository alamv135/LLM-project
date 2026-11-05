"""Ask tab, end to end: MedScout's real web server talking to the fake Ollama in tests/fake_ollama.py."""
import http.client
import json
import os
import shutil
import threading
import time
import unittest

from medscout.ask import web
from medscout.web.server import make_server

from . import fake_ollama
from .helpers import temp_settings


class AskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fake_ollama.start(0)
        cls.fake_port = cls.fake.server_address[1]
        web.DDG_HTML_URL = "http://127.0.0.1:%d/html/" % cls.fake_port
        web.ALLOW_PRIVATE = True       # the "internet" in these tests is the fake server on this computer

    @classmethod
    def tearDownClass(cls):
        web.ALLOW_PRIVATE = False
        cls.fake.shutdown()

    def setUp(self):
        fake_ollama.FakeOllama.requests.clear()
        fake_ollama.FakeOllama.delay = 0.0
        self.ms = temp_settings()
        self.server = make_server("127.0.0.1", 0, self.ms)
        self.port = self.server.server_address[1]
        self.app = self.server.app.ask
        self.settings = self.app.settings
        self.settings.update({"ollama_url": "http://127.0.0.1:%d" % self.fake_port})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.ms.runs_dir.parent, ignore_errors=True)

    # ----- helpers ---------------------------------------------------------------------------------
    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Content-Type": "application/json"} if body is not None else {}
        h.update(headers or {})
        conn.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None), headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def get(self, path):
        status, data = self.request("GET", path)
        return status, json.loads(data)

    def send(self, **req):
        status, data = self.request("POST", "/api/ask/send", req)
        self.assertEqual(status, 200)
        return [json.loads(line) for line in data.decode().splitlines() if line.strip()]

    def chats_sent_to_ollama(self):
        return [r for r in fake_ollama.FakeOllama.requests if "messages" in r]

    def upload(self, name, data):
        status, body = self.request("POST", "/api/ask/upload", raw=data,
                                    headers={"X-Filename": name, "Content-Type": "application/octet-stream"})
        return status, json.loads(body)

    # ----- tests ---------------------------------------------------------------------------------
    def test_status(self):
        status, s = self.get("/api/ask/status")
        self.assertEqual(status, 200)
        self.assertTrue(s["ollama"])
        self.assertTrue(s["model_installed"])
        self.assertIn("vision", s["capabilities"])
        self.assertEqual(s["max_context"], 262144)

    def test_simple_chat_is_streamed_and_saved(self):
        events = self.send(text="Hi there\nsecond line", web_search=False, think=False, medtech=False)
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[0], "chat")
        self.assertEqual(kinds[-1], "done")
        self.assertGreater(kinds.count("text"), 3)
        streamed = "".join(e["text"] for e in events if e["type"] == "text")
        done = events[-1]["message"]
        self.assertEqual(done["content"], streamed)
        self.assertEqual(done["stats"]["tokens"], 42)
        self.assertEqual(done["stats"]["tokens_per_s"], 30.0)
        chat_id = events[0]["chat"]["id"]
        _, chat = self.get("/api/ask/chats/" + chat_id)
        self.assertEqual(chat["title"], "Hi there")
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        body = self.chats_sent_to_ollama()[-1]
        self.assertNotIn("tools", body)
        self.assertIs(body["think"], False)
        self.assertEqual(body["options"]["num_ctx"], 32768)
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertIn("cannot search the web", body["messages"][0]["content"])
        self.assertIn("MedScout Ask", body["messages"][0]["content"])

        # a second message continues the same chat and carries the history
        events = self.send(chat_id=chat_id, text="And again", web_search=False)
        body = self.chats_sent_to_ollama()[-1]
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user", "assistant", "user"])
        _, lst = self.get("/api/ask/chats")
        self.assertEqual(len(lst["chats"]), 1)

    def test_web_search_with_citations(self):
        events = self.send(text="search the latest ollama news", web_search=True, think=True, medtech=False)
        kinds = [e["type"] for e in events]
        self.assertIn("thinking", kinds)
        self.assertLess(kinds.index("tool_start"), kinds.index("tool_end"))
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertTrue(end["ok"])
        self.assertEqual([r["n"] for r in end["results"]], [1, 2])
        self.assertTrue(end["results"][0]["url"].endswith("/article"))
        done = events[-1]["message"]
        self.assertIn("[1]", done["content"])
        self.assertEqual(len(done["sources"]), 2)
        self.assertEqual([p["type"] for p in done["parts"]], ["thinking", "tool", "thinking", "text"])
        self.assertIn("ms", done["parts"][0])
        first, second = self.chats_sent_to_ollama()[-2:]
        self.assertEqual({t["function"]["name"] for t in first["tools"]}, {"web_search", "fetch_url"})
        self.assertIs(first["think"], True)
        tool_msg = second["messages"][-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(tool_msg["tool_name"], "web_search")
        self.assertIn("[1] Ollama 0.12 release notes", tool_msg["content"])
        self.assertEqual(second["messages"][-2]["tool_calls"][0]["function"]["name"], "web_search")
        # next turn: the earlier answer is sent with its source list
        self.send(chat_id=events[0]["chat"]["id"], text="thanks", web_search=True)
        hist = self.chats_sent_to_ollama()[-1]["messages"]
        self.assertIn("Sources of this answer: [1] Ollama 0.12 release notes", hist[-2]["content"])

    def test_fetch_url(self):
        url = "http://127.0.0.1:%d/article" % self.fake_port
        events = self.send(text="summarize " + url, web_search=True)
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertTrue(end["ok"], end)
        self.assertEqual(end["label"], "Read Ollama 0.12 release notes")
        tool_msg = self.chats_sent_to_ollama()[-1]["messages"][-1]
        self.assertIn("This release adds a web search API", tool_msg["content"])
        self.assertNotIn("tracking", tool_msg["content"])

    def test_blocked_fetch_is_reported_to_model(self):
        web.ALLOW_PRIVATE = False
        try:
            events = self.send(text="read http://127.0.0.1:%d/article" % self.fake_port, web_search=True)
        finally:
            web.ALLOW_PRIVATE = True
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertFalse(end["ok"])
        self.assertIn("local or private address", end["error"])
        self.assertIn("Tool error", self.chats_sent_to_ollama()[-1]["messages"][-1]["content"])
        self.assertIn("could not get", events[-1]["message"]["content"])

    def test_capabilities_respected(self):
        events = self.send(text="search something", model="tiny:1b", web_search=True, think=True)
        notices = [e["text"] for e in events if e["type"] == "notice"]
        self.assertTrue(any("can't use tools" in n for n in notices))
        self.assertTrue(any("no thinking mode" in n for n in notices))
        body = self.chats_sent_to_ollama()[-1]
        self.assertNotIn("tools", body)
        self.assertNotIn("think", body)
        self.assertEqual(body["options"]["num_ctx"], 4096)     # capped at the model's maximum
        self.assertIsNone(events[-1]["message"]["error"])

    def test_errors_are_friendly(self):
        events = self.send(text="hello", model="nope:7b")
        self.assertIn("not found", events[-1]["message"]["error"])
        self.assertIn("ollama pull", events[-1]["message"]["error"])
        self.settings.update({"ollama_url": "http://127.0.0.1:9"})
        events = self.send(text="hello")
        self.assertIn("Can't reach Ollama", events[-1]["message"]["error"])
        _, s = self.get("/api/ask/status")
        self.assertFalse(s["ollama"])

    def test_stop(self):
        result = {}

        def run():
            result["events"] = self.send(text="please be slow", web_search=False)
        t = threading.Thread(target=run)
        t.start()
        for _ in range(100):
            chats = self.app.store.list()
            if chats and self.app.chats.is_busy(chats[0]["id"]):
                break
            time.sleep(0.05)
        time.sleep(0.8)
        status, body = self.request("POST", "/api/ask/chats/%s/stop" % chats[0]["id"], {})
        self.assertEqual(json.loads(body), {"stopped": True})
        t.join(10)
        done = result["events"][-1]["message"]
        self.assertTrue(done["stopped"])
        self.assertTrue(done["content"].startswith("Hello!"))
        self.assertLess(len(done["content"]), 60)                # it really stopped early

    def test_tool_loop_is_limited(self):
        self.settings.update({"max_tool_rounds": 2})
        orig = fake_ollama.FakeOllama._script

        def always_search(handler, body):
            return [{"message": {"role": "assistant", "content": "", "tool_calls": [
                        {"function": {"name": "web_search", "arguments": {"query": "q%d" % len(body["messages"])}}}]},
                     "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True, "eval_count": 1, "eval_duration": 1}]
        fake_ollama.FakeOllama._script = always_search
        try:
            events = self.send(text="search forever", web_search=True)
        finally:
            fake_ollama.FakeOllama._script = orig
        bodies = self.chats_sent_to_ollama()
        self.assertEqual(len(bodies), 3)                      # 2 rounds with tools + 1 final round
        self.assertNotIn("tools", bodies[-1])
        self.assertEqual(bodies[-1]["messages"][-1]["role"], "system")
        done = events[-1]["message"]
        self.assertEqual(sum(1 for p in done["parts"] if p["type"] == "tool"), 2)
        self.assertTrue(any("did not write an answer" in n for n in done["notices"]))

    def test_retry_replaces_last_answer(self):
        events = self.send(text="first", web_search=False)
        chat_id = events[0]["chat"]["id"]
        self.send(chat_id=chat_id, retry=True, web_search=False)
        _, chat = self.get("/api/ask/chats/" + chat_id)
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])

    def test_files_images_and_long_documents(self):
        status, meta = self.upload("notes.md", b"# Notes\nShort file")
        self.assertEqual((status, meta["kind"], meta["chars"]), (200, "text", 18))
        status, img = self.upload("photo.png", b"\x89PNG\r\n\x1a\nfake-image")
        self.assertEqual(img["kind"], "image")
        events = self.send(text="what are these?", attachments=[meta["id"], img["id"]], web_search=False)
        user = self.chats_sent_to_ollama()[-1]["messages"][-1]
        self.assertTrue(user["content"].startswith('<file name="notes.md">\n# Notes\nShort file\n</file>'))
        self.assertTrue(user["content"].endswith("what are these?"))
        self.assertEqual(len(user["images"]), 1)
        status, raw = self.request("GET", "/api/ask/files/" + img["id"])
        self.assertEqual(raw, b"\x89PNG\r\n\x1a\nfake-image")

        # a document far bigger than the context window: cut, plus the search_files tool
        self.settings.update({"num_ctx": 8192})
        filler = "Background text about something else entirely.\n" * 3000
        big = ("Chapter 1\n" + filler + "The secret code is 4417.\n" + filler).encode()
        _, meta = self.upload("book.txt", big)
        events = self.send(chat_id=events[0]["chat"]["id"], text="look in the file for the secret code",
                           attachments=[meta["id"]], web_search=False, medtech=False)
        first, second = self.chats_sent_to_ollama()[-2:]
        self.assertIn("Use the search_files tool", first["messages"][-1]["content"])
        self.assertLess(len(first["messages"][-1]["content"]), 8192 * 3)
        self.assertEqual([t["function"]["name"] for t in first["tools"]], ["search_files"])
        self.assertIn("The secret code is 4417", second["messages"][-1]["content"])
        self.assertEqual(events[-1]["message"]["parts"][0]["name"], "search_files")

        # deleting the chat deletes its files
        status, _ = self.request("DELETE", "/api/ask/chats/" + events[0]["chat"]["id"])
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/ask/files/" + meta["id"])[0], 404)

    def test_bad_uploads(self):
        status, body = self.upload("tool.exe", b"MZ\x00\x00\x00binary")
        self.assertEqual(status, 400)
        self.assertIn("Can't read", body["error"])
        status, body = self.upload("broken.docx", b"not a zip")
        self.assertEqual(status, 400)

    def test_old_messages_dropped_to_fit_context(self):
        self.settings.update({"num_ctx": 4096})
        events = self.send(text="start", web_search=False)
        chat_id = events[0]["chat"]["id"]
        for i in range(6):
            self.send(chat_id=chat_id, text=("long message %d " % i) + "words " * 900, web_search=False)
        events = self.send(chat_id=chat_id, text="last question", web_search=False)
        self.assertTrue(any(e["type"] == "context" for e in events))
        msgs = self.chats_sent_to_ollama()[-1]["messages"]
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[-1]["content"], "last question")
        self.assertLess(sum(len(m["content"]) for m in msgs), 4096 * 3.6)

    def test_security_checks(self):
        status, _ = self.request("POST", "/api/ask/settings", {"theme": "dark"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/api/ask/settings", headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/ask/static/../config.py")
        self.assertEqual(status, 404)
        status, _ = self.request("GET", "/api/ask/chats/..%2F..%2Fsettings")
        self.assertEqual(status, 404)
        status, body = self.request("POST", "/api/ask/settings", {"ollama_api_key": "abc"},
                                    headers={"Origin": "http://127.0.0.1:%d" % self.port})
        self.assertEqual(status, 200)
        self.assertNotIn(b"abc", body)

    def test_pull_progress(self):
        status, data = self.request("POST", "/api/ask/pull", {"model": "qwen3.5:9b"})
        lines = [json.loads(x) for x in data.decode().splitlines()]
        self.assertEqual(lines[0]["status"], "pulling manifest")
        self.assertEqual(lines[-1]["status"], "success")
        self.assertEqual(self.request("POST", "/api/ask/pull", {"model": "bad name; rm"})[0], 400)

    def test_page_is_served(self):
        status, html = self.request("GET", "/ask/")
        self.assertEqual(status, 200)
        self.assertIn(b"MedScout Ask", html)
        self.assertEqual(self.request("GET", "/ask")[0], 302)
        for path in ("/ask/static/app.js", "/ask/static/app.css", "/ask/static/markdown.js", "/ask/static/vendor/marked.min.js",
                     "/ask/static/vendor/purify.min.js", "/ask/static/vendor/katex/katex.min.js",
                     "/ask/static/vendor/hljs/highlight.min.js", "/ask/static/vendor/katex/fonts/KaTeX_Main-Regular.woff2"):
            self.assertEqual(self.request("GET", path)[0], 200, path)


    # ----- MedScout integration ---------------------------------------------------------------------
    def test_medtech_tools_on_demo_mirror(self):
        self.settings.update({"medtech_backend": "nordleaf"})
        events = self.send(text="What does the FDA have on Nordleaf?", web_search=False, medtech=True)
        first, second = self.chats_sent_to_ollama()[-2:]
        names = {t["function"]["name"] for t in first["tools"]}
        self.assertEqual(names, {"fda_510k_search", "fda_pma_search", "fda_classification_lookup",
                                 "clinicaltrials_search", "pubmed_search"})
        for t in first["tools"]:                     # only simple schemas reach Ollama
            self.assertNotIn("anyOf", json.dumps(t))
        self.assertIn("SYNTHETIC demo data for the fictional case 'nordleaf'", first["messages"][0]["content"])
        self.assertIn("a 510(k) is a clearance, not an approval", first["messages"][0]["content"])
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertTrue(end["ok"], end)
        self.assertEqual(end["label"], 'Searched FDA 510(k) clearances for "Nordleaf Cardio"')
        top = end["results"][0]
        self.assertEqual(top["n"], 1)
        self.assertIn("K251234", top["title"])
        self.assertTrue(top["url"].startswith("https://www.accessdata.fda.gov/") and top["url"].endswith("K251234"))
        tool_msg = second["messages"][-1]
        self.assertEqual(tool_msg["tool_name"], "fda_510k_search")
        self.assertIn("SYNTHETIC", tool_msg["content"])
        self.assertIn('"k_number": "K251234"', tool_msg["content"])
        done = events[-1]["message"]
        self.assertIn("[1]", done["content"])
        self.assertEqual(done["sources"][0]["url"], top["url"])

    def test_medtech_bad_arguments_go_back_to_the_model(self):
        self.settings.update({"medtech_backend": "nordleaf"})
        events = self.send(text="look up bad k number", web_search=False, medtech=True)
        end = next(e for e in events if e["type"] == "tool_end")
        self.assertFalse(end["ok"])
        self.assertIn("invalid arguments", end["error"])
        self.assertIn("Tool error (permanent)", self.chats_sent_to_ollama()[-1]["messages"][-1]["content"])

    def test_medtech_off_means_no_medtech_tools(self):
        self.send(text="What does the FDA have on Nordleaf?", web_search=True, medtech=False)
        names = {t["function"]["name"] for t in self.chats_sent_to_ollama()[-1]["tools"]}
        self.assertEqual(names, {"web_search", "fetch_url"})

    def test_attach_run_brief(self):
        run_id = "20261105-101500-abc123"
        run_dir = self.ms.runs_dir / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "brief.md").write_text("# Investment Readiness Brief\nClaim C1: FDA cleared (510(k) K251234).")
        (run_dir / "state.json").write_text(json.dumps({"company": "Nordleaf Cardio"}))
        status, body = self.request("POST", "/api/ask/attach-run", {"run_id": run_id})
        meta = json.loads(body)
        self.assertEqual(status, 200, meta)
        self.assertEqual(meta["name"], "MedScout brief - Nordleaf Cardio (%s).md" % run_id)
        self.assertEqual(meta["kind"], "text")
        events = self.send(text="summarise", attachments=[meta["id"]], web_search=False, medtech=False)
        user = self.chats_sent_to_ollama()[-1]["messages"][-1]["content"]
        self.assertIn("Claim C1: FDA cleared", user)
        self.assertIsNone(events[-1]["message"]["error"])
        self.assertEqual(self.request("POST", "/api/ask/attach-run", {"run_id": "../../etc"})[0], 400)
        self.assertEqual(self.request("POST", "/api/ask/attach-run", {"run_id": "20261105-101500-ffffff"})[0], 400)

    def test_console_has_ask_tab(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b'href="#ask"', html)
        self.assertIn(b'id="view-ask"', html)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/ask/")
        resp = conn.getresponse()
        resp.read()
        self.assertIn("frame-ancestors 'self'", resp.getheader("Content-Security-Policy"))
        conn.close()
        # other MedScout routes still work next to /api/ask/
        self.assertEqual(self.request("GET", "/api/config")[0], 200)

    def test_ollama_address_follows_console_connection(self):
        self.settings.update({"ollama_url": ""})
        old = os.environ.get("OLLAMA_HOST")
        try:
            os.environ["OLLAMA_HOST"] = "127.0.0.1:%d" % self.fake_port      # what the Model connection tab sets
            _, s = self.get("/api/ask/status")
            self.assertTrue(s["ollama"])
            self.assertEqual(s["ollama_url"], "http://127.0.0.1:%d" % self.fake_port)
            os.environ.pop("OLLAMA_HOST")
            self.assertEqual(self.settings.ollama_url(), "http://127.0.0.1:11434")
        finally:
            if old is None:
                os.environ.pop("OLLAMA_HOST", None)
            else:
                os.environ["OLLAMA_HOST"] = old

if __name__ == "__main__":
    unittest.main()
