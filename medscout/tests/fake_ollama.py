"""A fake Ollama server for the Ask tests and demos (no real model needed).

It speaks the parts of the Ollama API that MedScout Ask uses and answers with scripted text:
  * "fda" (with the MedTech tools on)          -> calls fda_510k_search for Nordleaf, answers citing [1]
  * "bad k number"                             -> calls fda_510k_search with an invalid K-number
  * a message containing "search" or "latest"  -> calls web_search, then answers citing [1] [2]
  * a message containing a URL                 -> calls fetch_url on it
  * "slow"                                     -> streams slowly (for testing Stop)
  * "markdown"                                 -> a reply with headings, a table, code and math
It also serves a DuckDuckGo-like results page at /html/ and a test article at /article.

Run it on its own:  python3 tests/fake_ollama.py --port 11500
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

MODELS = {
    "qwen3.5:9b": {"capabilities": ["completion", "tools", "vision", "thinking"], "ctx": 262144,
                   "family": "qwen35", "size": "9.7B"},
    "llama3.2:3b": {"capabilities": ["completion", "tools"], "ctx": 131072, "family": "llama", "size": "3.2B"},
    "tiny:1b": {"capabilities": ["completion"], "ctx": 4096, "family": "tiny", "size": "1B"},
}

DDG_PAGE = """<!DOCTYPE html><html><body><div class="serp__results">
<div class="result results_links results_links_deep result--ad"><div class="links_main links_deep result__body">
<h2 class="result__title"><a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=ads.example">Buy now</a></h2>
<a class="result__snippet" href="https://duckduckgo.com/y.js?x">An advert</a></div></div>
<div class="result results_links results_links_deep web-result"><div class="links_main links_deep result__body">
<h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=__BASE__%2Farticle&amp;rut=abc">Ollama 0.12 release <b>notes</b></a></h2>
<div class="result__extras"><a class="result__url" href="x">example.org</a></div>
<a class="result__snippet" href="x">Ollama 0.12 adds <b>web search</b> and faster model loading.</a></div></div>
<div class="result results_links results_links_deep web-result"><div class="links_main links_deep result__body">
<h2 class="result__title"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fqwen.example%2Fblog%2Fqwen3.5&amp;rut=def">Qwen3.5: towards native multimodal agents</a></h2>
<a class="result__snippet" href="x">Qwen3.5 small models (0.8B to 9B) support <b>tools</b>, vision and a 256K context.</a></div></div>
</div></body></html>"""

ARTICLE = """<!DOCTYPE html><html><head><title>Ollama 0.12 release notes</title></head><body>
<nav><a href="/">Home</a><a href="/blog">Blog</a></nav>
<div class="cookie-banner">We use cookies</div>
<main><article><h1>Ollama 0.12 release notes</h1>
<p>This release adds a <b>web search</b> API and loads models 30% faster.</p>
<p>It also improves tool calling for Qwen models.</p></article></main>
<footer>Copyright 2026</footer><script>var tracking = 1;</script></body></html>"""


class FakeOllama(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: List[Dict[str, Any]] = []
    delay = 0.0          # seconds between streamed words

    def log_message(self, *a: Any) -> None:
        pass

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, page: str) -> None:
        body = page.replace("__BASE__", "http%3A%2F%2F127.0.0.1%3A" + str(self.server.server_address[1])).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/version":
            return self._json({"version": "0.12.9-fake"})
        if self.path == "/api/tags":
            return self._json({"models": [{"name": n, "size": 6_600_000_000,
                                           "details": {"parameter_size": m["size"], "family": m["family"]}}
                                          for n, m in MODELS.items()]})
        if self.path == "/article":
            return self._html(ARTICLE)
        if self.path == "/_requests":
            return self._json(self.requests)
        return self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if self.path in ("/html/", "/lite/"):
            return self._html(DDG_PAGE)
        body = json.loads(raw or b"{}")
        if self.path == "/api/show":
            m = MODELS.get(body.get("model"))
            if not m:
                return self._json({"error": "model '%s' not found" % body.get("model")}, 404)
            return self._json({"capabilities": m["capabilities"], "details": {"family": m["family"],
                               "parameter_size": m["size"]}, "model_info": {m["family"] + ".context_length": m["ctx"]}})
        if self.path == "/api/pull":
            return self._stream([{"status": "pulling manifest"},
                                 {"status": "downloading", "digest": "sha256:abc", "total": 100, "completed": 50},
                                 {"status": "downloading", "digest": "sha256:abc", "total": 100, "completed": 100},
                                 {"status": "success"}])
        if self.path == "/api/chat":
            self.requests.append(body)
            if body.get("model") not in MODELS:
                return self._json({"error": "model '%s' not found" % body.get("model")}, 404)
            if body.get("tools") and "tools" not in MODELS[body["model"]]["capabilities"]:
                return self._json({"error": "registry.ollama.ai/library/x does not support tools"}, 400)
            return self._stream(self._script(body))
        return self._json({"error": "not found"}, 404)

    def _stream(self, chunks: List[Dict[str, Any]]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for c in chunks:
                data = (json.dumps(c) + "\n").encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()
                if c.get("_sleep"):
                    time.sleep(c["_sleep"])
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            FakeOllama.requests.append({"_client_disconnected": True})

    # ----- the scripted "model" ------------------------------------------------------------------
    def _script(self, body: Dict[str, Any]) -> List[Dict[str, Any]]:
        msgs = body.get("messages") or []
        last = msgs[-1] if msgs else {}
        user = next((m for m in reversed(msgs) if m.get("role") == "user"), {})
        text = user.get("content", "")
        tools = {t["function"]["name"] for t in body.get("tools") or []}
        out: List[Dict[str, Any]] = []
        delay = 0.25 if "slow" in text else self.delay

        def say(s: str, kind: str = "content") -> None:
            for w in re.findall(r"\S+\s*|\s+", s):
                out.append({"message": {"role": "assistant", kind: w}, "done": False, "_sleep": delay})

        if body.get("think"):
            say("The user asks something. Let me think about what they need. ", "thinking")

        url = re.search(r"https?://\S+", text)
        if last.get("role") == "tool":
            tool_text = last.get("content", "")
            nums = re.findall(r"^\[(\d+)\]", tool_text, re.M)
            if "Tool error" in tool_text:
                say("I could not get that information: the tool failed.")
            elif nums and last.get("tool_name") != "web_search":
                say("The records show a 510(k) clearance [%s], which is not a PMA approval." % nums[0])
            elif nums:
                say("According to the results, Ollama 0.12 adds web search [%s]. " % nums[0])
                if len(nums) > 1:
                    say("Qwen3.5 small models support tools and vision [%s]." % nums[1])
            else:
                say("Here is what I found: " + tool_text[:80])
        elif "bad k number" in text.lower() and "fda_510k_search" in tools:
            out.append({"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "fda_510k_search", "arguments": {"k_number": "123"}}}]}, "done": False})
        elif "fda" in text.lower() and "fda_510k_search" in tools:
            out.append({"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "fda_510k_search", "arguments": {"applicant": "Nordleaf Cardio"}}}]}, "done": False})
        elif url and "fetch_url" in tools:
            out.append({"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "fetch_url", "arguments": {"url": url.group(0)}}}]}, "done": False})
        elif ("search" in text.lower() or "latest" in text.lower()) and "web_search" in tools:
            out.append({"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "web_search", "arguments": {"query": "ollama latest release"}}}]}, "done": False})
        elif "look in the file" in text and "search_files" in tools:
            out.append({"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "search_files", "arguments": {"query": "secret code"}}}]}, "done": False})
        elif user.get("images"):
            say("I can see %d image(s). It looks like a small test picture." % len(user["images"]))
        elif "<file name=" in text:
            names = re.findall(r'<file name="([^"]+)">', text)
            say("You attached **%s**. It is about %d characters long." % (", ".join(names), len(text)))
        elif "markdown" in text.lower():
            say("## Quick answer\n\nHere is a **sample** with a list, a table, code and math.\n\n"
                "- First point\n- Second point with `inline code`\n\n"
                "| Model | Size |\n|---|---|\n| qwen3.5:9b | 6.6 GB |\n| llama3.2:3b | 2.0 GB |\n\n"
                "```python\ndef hello(name):\n    return f\"Hello, {name}!\"\n```\n\n"
                "The area of a circle is $A = \\pi r^2$, and\n\n$$\\int_0^1 x^2\\,dx = \\frac{1}{3}$$\n")
        else:
            say("Hello! I am a fake model. You said: " + text[-200:])
        out.append({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                    "prompt_eval_count": 321, "eval_count": 42, "eval_duration": 1_400_000_000})
        return out


def start(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), FakeOllama)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11500)
    ap.add_argument("--delay", type=float, default=0.03)
    a = ap.parse_args()
    FakeOllama.delay = a.delay
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), FakeOllama)
    print("fake Ollama on http://127.0.0.1:%d" % a.port)
    srv.serve_forever()
