"""A fake OpenRouter (OpenAI-compatible API) for tests: model list, JSON answers and SSE streaming.

  GET  /api/v1/models               catalog with prices (USD per token, as strings) and abilities
  POST /api/v1/chat/completions     stream=false -> MedScout's mock policy answer (valid run JSON)
                                    stream=true  -> scripted chat: "search" -> a web_search tool call
                                    split over several chunks; after a tool result -> an answer citing [1]
Every answer reports usage with a cost, as OpenRouter does. Requests are kept in FakeOpenRouter.requests.
Under /deepseek/ it plays DeepSeek's own API, with its documented quirks:
  * usage has no cost but reports prompt_cache_hit_tokens
  * JSON mode needs the word "json" in the prompt (else HTTP 400); content is padded with spaces;
    it sometimes returns EMPTY content (FakeOpenRouter.deepseek_empty = how many times next)
  * a max_tokens of 10 or less cuts the answer off (finish_reason "length")
  * streamed answers start with reasoning_content; an assistant tool-call message sent back without
    its reasoning_content is refused with HTTP 400
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

COST = 0.0123            # USD reported for every call

CATALOG = {"data": [
    {"id": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek", "context_length": 131072,
     "pricing": {"prompt": "0.0000002", "completion": "0.0000008"},
     "supported_parameters": ["tools", "response_format", "structured_outputs", "reasoning"],
     "architecture": {"input_modalities": ["text"]}},
    {"id": "google/gemini-3.8-flash", "name": "Gemini", "context_length": 1000000,
     "pricing": {"prompt": "0.00000075", "completion": "0.00000375"},
     "supported_parameters": ["tools", "response_format", "structured_outputs"],
     "architecture": {"input_modalities": ["text", "image"]}},
    {"id": "anthropic/claude-sonnet-5.5", "name": "Claude", "context_length": 1000000,
     "pricing": {"prompt": "0.000003", "completion": "0.000015"},
     "supported_parameters": ["tools", "response_format", "reasoning"],
     "architecture": {"input_modalities": ["text", "image"]}},
]}


DEEPSEEK_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}


class QuietServer(ThreadingHTTPServer):
    """Clients closing kept-alive connections are normal here; do not print them."""
    def handle_error(self, request, client_address):  # noqa: ANN001
        import sys
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


class FakeOpenRouter(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: List[Dict[str, Any]] = []
    deepseek_empty = 0

    def log_message(self, *a: Any) -> None:
        pass

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/deepseek/models":
            return self._json({"object": "list", "data": [{"id": m} for m in sorted(DEEPSEEK_MODELS)]})
        if self.path == "/api/v1/models":
            return self._json(CATALOG)
        return self._json({"error": {"message": "not found"}}, 404)

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        body["_auth"] = self.headers.get("Authorization")
        body["_path"] = self.path
        FakeOpenRouter.requests.append(body)
        direct = self.path == "/deepseek/chat/completions"
        if self.path != "/api/v1/chat/completions" and not direct:
            return self._json({"error": {"message": "not found"}}, 404)
        if not (body.get("_auth") or "").startswith("Bearer "):
            return self._json({"error": {"message": "No auth credentials found"}}, 401)
        if body.get("model") not in (DEEPSEEK_MODELS if direct else {m["id"] for m in CATALOG["data"]}):
            return self._json({"error": {"message": "model not found"}}, 404)
        usage: Dict[str, Any] = {"prompt_tokens": 300, "completion_tokens": 40}
        if direct:
            usage.update(prompt_cache_hit_tokens=200, prompt_cache_miss_tokens=100)
            if (body.get("response_format") or {}).get("type") == "json_object" and \
                    "json" not in json.dumps(body["messages"]).lower():
                return self._json({"error": {"message": "Prompt must contain the word 'json' in some form to use "
                                                        "'response_format' of type 'json_object'.", "type": "invalid_request_error"}}, 400)
            for m in body["messages"]:
                if m.get("role") == "assistant" and m.get("tool_calls") and "reasoning_content" not in m:
                    return self._json({"error": {"message": "Missing `reasoning_content` field in the assistant "
                                                            "message", "type": "invalid_request_error"}}, 400)
        else:
            usage["cost"] = COST
        if not body.get("stream"):
            from medscout import mock_policy
            msgs = [{"role": m["role"], "content": m["content"] if isinstance(m["content"], str) else ""}
                    for m in body["messages"]]
            text, finish = mock_policy.respond(msgs), "stop"
            if direct:
                if FakeOpenRouter.deepseek_empty > 0:
                    FakeOpenRouter.deepseek_empty -= 1
                    text = ""
                elif (body.get("max_tokens") or 9999) <= 10:
                    text, finish = text[:15], "length"
                else:
                    text = "\n  " + text + "  \n"
            return self._json({"model": body["model"], "usage": usage, "choices": [
                {"message": {"role": "assistant", "content": text,
                             **({"reasoning_content": "Let me think."} if direct else {})}, "finish_reason": finish}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(obj: Any) -> None:
            data = (": OPENROUTER PROCESSING\n\n" if obj is None else
                    "data: %s\n\n" % (obj if isinstance(obj, str) else json.dumps(obj))).encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        def delta(d: Dict[str, Any], finish: Any = None) -> Dict[str, Any]:
            return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}

        msgs = body["messages"]
        last = msgs[-1]
        user = next((m for m in reversed(msgs) if m["role"] == "user"), {})
        text = user.get("content") if isinstance(user.get("content"), str) else \
            " ".join(p.get("text", "") for p in user.get("content") or [] if isinstance(p, dict))
        tools = {t["function"]["name"] for t in body.get("tools") or []}
        send(None)
        if direct:
            send(delta({"role": "assistant", "reasoning_content": "DeepSeek thinks first. "}))
        if last["role"] == "tool":
            send(delta({"role": "assistant", "content": "Cloud answer from the results "}))
            send(delta({"content": "[1]."}, "stop"))
        elif "search" in text.lower() and "web_search" in tools:
            send(delta({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_abc", "type": "function",
                                                             "function": {"name": "web_search", "arguments": ""}}]}))
            send(delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"query": "ollama '}}]}))
            send(delta({"tool_calls": [{"index": 0, "function": {"arguments": 'release"}'}}]}, "tool_calls"))
        else:
            if body.get("reasoning"):
                send(delta({"role": "assistant", "reasoning": "Thinking in the cloud. "}))
            for word in ("Cloud ", "model ", "%s " % body["model"], "says hello."):
                send(delta({"content": word}))
        send({"choices": [], "usage": usage})
        send("[DONE]")
        self.wfile.write(b"0\r\n\r\n")


def start() -> ThreadingHTTPServer:
    srv = QuietServer(("127.0.0.1", 0), FakeOpenRouter)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
