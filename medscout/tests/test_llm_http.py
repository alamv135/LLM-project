"""Exercise the real HTTP model clients against local fake endpoints.

The fake servers answer with the mock policy, so this checks request/response
handling of the Ollama and OpenAI-compatible clients (payload shape, JSON mode,
token usage parsing) end to end without network access or API keys.
"""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from medscout import mock_policy
from medscout.llm import OllamaLLM, OpenAICompatLLM
from medscout.workflow import Workflow

from .helpers import env, new_state, temp_settings

SEEN: list[dict] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        text = mock_policy.respond(body["messages"])
        if self.path.startswith("/api/chat"):
            out = {"message": {"role": "assistant", "content": text}, "prompt_eval_count": 111, "eval_count": 22}
        else:
            out = {"choices": [{"message": {"role": "assistant", "content": text}}],
                   "usage": {"prompt_tokens": 333, "completion_tokens": 44}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TestHTTPClients(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_openai_compatible_client(self):
        SEEN.clear()
        with env(OPENAI_BASE_URL=self.url + "/v1", OPENAI_API_KEY="test-key", NO_PROXY="127.0.0.1"):
            r = OpenAICompatLLM("test-model").complete([{"role": "system", "content": "s"},
                                                        {"role": "user", "content": "PHASE: plan\nCLAIMS_JSON:\n[]\nEND_CLAIMS"}])
        self.assertEqual(json.loads(r.text), {"tasks": []})
        self.assertEqual((r.tokens_in, r.tokens_out), (333, 44))
        self.assertEqual(SEEN[0]["path"], "/v1/chat/completions")
        self.assertEqual(SEEN[0]["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(SEEN[0]["auth"], "Bearer test-key")

    def test_ollama_client_uses_schema_format(self):
        SEEN.clear()
        schema = {"type": "object", "properties": {"tasks": {"type": "array"}}}
        with env(OLLAMA_HOST=self.url, NO_PROXY="127.0.0.1"):
            r = OllamaLLM("llama3.2:3b").complete([{"role": "system", "content": "s"},
                                                   {"role": "user", "content": "PHASE: plan\nCLAIMS_JSON:\n[]\nEND_CLAIMS"}],
                                                  schema)
        self.assertEqual((r.tokens_in, r.tokens_out), (111, 22))
        self.assertEqual(SEEN[0]["body"]["format"], schema)       # constrained decoding request
        self.assertFalse(SEEN[0]["body"]["stream"])

    def test_full_workflow_through_openai_client(self):
        s = temp_settings(provider="openai", model="test-model", price_in_per_m=1.0, price_out_per_m=2.0)
        st, cdir = new_state(focus=["regulatory"])
        with env(OPENAI_BASE_URL=self.url + "/v1", OPENAI_API_KEY="k", NO_PROXY="127.0.0.1"):
            Workflow(s, st, case_dir=cdir, echo=False).run()
        self.assertEqual(st.phase, "done")
        self.assertGreater(st.counters.cost_usd, 0)
        self.assertEqual(st.counters.tokens_in, 333 * st.counters.model_calls)


if __name__ == "__main__":
    unittest.main()
