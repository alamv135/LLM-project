"""Two ways to reach a model, behind the same two operations:

  complete(model_id, messages, schema=...)  -> LLMResult      one JSON answer (MedScout runs, router)
  stream(body, cancel)                      -> chunks         streamed chat with tools (Ask)

Chunks always look like Ollama's /api/chat stream, so the Ask chat loop does not care where the
model runs:  {"message": {"content" | "thinking" | "tool_calls"}, "done": bool, ...usage...}

OllamaProvider  local models (native API: context size, thinking switch, JSON-schema output)
OpenAIProvider  any OpenAI-compatible endpoint; with "openrouter": true also OpenRouter's extras
                (cost in the usage block, attribution headers, reasoning switch)
"""

from __future__ import annotations

import http.client
import json
import os
import time
from typing import Any, Iterator

from ..ask.ollama_client import Cancelled  # noqa: F401 - one "Stop" exception for every provider
from ..llm import LLMError, LLMResult, estimate_tokens, messages_tokens
from .http import request


def _check_cancel(cancel: Any) -> None:
    if cancel is not None and getattr(cancel, "cancelled", False):
        raise Cancelled()


# ---------------------------------------------------------------------------------------- Ollama
class OllamaProvider:
    kind = "ollama"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def complete(self, model_id: str, messages: list[dict[str, Any]], schema: dict[str, Any] | None = None, *,
                 temperature: float = 0.0, timeout: float = 120.0, num_ctx: int | None = None,
                 think: bool | None = None, max_tokens: int | None = None) -> LLMResult:
        fmt: Any = schema if (schema and "oneOf" not in json.dumps(schema)) else ("json" if schema else None)
        options: dict[str, Any] = {"temperature": temperature,
                                   "num_ctx": num_ctx or int(os.environ.get("OLLAMA_NUM_CTX", "8192"))}
        if max_tokens:
            options["num_predict"] = max_tokens
        body: dict[str, Any] = {"model": model_id, "messages": messages, "stream": False, "options": options}
        if fmt:
            body["format"] = fmt
        if think is not None:
            body["think"] = think
        t0 = time.perf_counter()
        data = _call(self.base_url + "/api/chat", body, {}, timeout)
        return LLMResult(data.get("message", {}).get("content", ""), int(data.get("prompt_eval_count", 0)),
                         int(data.get("eval_count", 0)), round((time.perf_counter() - t0) * 1000, 1), model_id,
                         cost_usd=0.0)

    def stream(self, body: dict[str, Any], cancel: Any = None) -> Iterator[dict[str, Any]]:
        from ..ask.ollama_client import OllamaClient
        return OllamaClient(self.base_url).chat(body, cancel)


# ------------------------------------------------------------------------------ OpenAI-compatible
class OpenAIProvider:
    kind = "openai"

    def __init__(self, base_url: str, api_key: str = "", openrouter: bool = False, reasoning_field: str = "",
                 empty_json_retries: int = 0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.openrouter = openrouter
        # DeepSeek: earlier "reasoning_content" must be sent back while a tool-calling turn continues
        self.reasoning_field = reasoning_field
        # DeepSeek: JSON mode "may occasionally return empty content" -> ask again this many times
        self.empty_json_retries = empty_json_retries

    def _headers(self) -> dict[str, str]:
        h = {}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        if self.openrouter:     # optional attribution headers OpenRouter asks apps to send
            h["HTTP-Referer"] = "https://github.com/alamv135/LLM-project"
            h["X-Title"] = "MedScout"
        return h

    def complete(self, model_id: str, messages: list[dict[str, Any]], schema: dict[str, Any] | None = None, *,
                 temperature: float = 0.0, timeout: float = 120.0, num_ctx: int | None = None,
                 think: bool | None = None, max_tokens: int | None = None, json_schema: bool = False) -> LLMResult:
        body: dict[str, Any] = {"model": model_id, "messages": to_openai(messages, self.reasoning_field),
                                "temperature": temperature}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if schema:
            body["response_format"] = ({"type": "json_schema", "json_schema": {"name": "medscout", "schema": schema}}
                                       if json_schema else {"type": "json_object"})
        if self.openrouter:
            body["usage"] = {"include": True}
            if think is not None:
                body["reasoning"] = {"enabled": bool(think)}
        t0 = time.perf_counter()
        tokens_in = tokens_out = cached = 0
        for attempt in range(1 + (self.empty_json_retries if schema else 0)):
            data = _call(self.base_url + "/chat/completions", body, self._headers(), timeout)
            try:
                choice = data["choices"][0]
                msg = choice["message"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"unexpected response shape: {str(data)[:200]}") from exc
            usage = data.get("usage") or {}
            tokens_in += int(usage.get("prompt_tokens") or messages_tokens(messages))
            tokens_out += int(usage.get("completion_tokens") or 0)
            cached += int(usage.get("prompt_cache_hit_tokens") or 0)
            text = msg.get("content") or ""
            if isinstance(text, list):          # some providers return content parts
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            if choice.get("finish_reason") == "length" and schema:
                raise LLMError(f"the answer was cut off at max_tokens={max_tokens}; the JSON is incomplete "
                               "(raise max_output_tokens in config/models.json)")
            if text.strip() or not schema:
                break
        if schema and not text.strip():
            raise LLMError("the model returned empty content in JSON mode (a known DeepSeek behaviour)")
        return LLMResult(text.strip(), tokens_in, tokens_out or estimate_tokens(text),
                         round((time.perf_counter() - t0) * 1000, 1), data.get("model") or model_id,
                         estimated_tokens=not usage, cost_usd=_float(usage.get("cost")), cached_tokens=cached)

    def stream(self, body: dict[str, Any], cancel: Any = None) -> Iterator[dict[str, Any]]:
        """Streamed chat (Server-Sent Events), translated into Ollama-style chunks."""
        options = body.get("options") or {}
        payload: dict[str, Any] = {"model": body["model"], "messages": to_openai(body["messages"], self.reasoning_field),
                                   "stream": True,
                                   "stream_options": {"include_usage": True}}
        if options.get("temperature") is not None:
            payload["temperature"] = options["temperature"]
        if body.get("max_tokens"):
            payload["max_tokens"] = body["max_tokens"]
        if body.get("tools"):
            payload["tools"] = body["tools"]
        if self.openrouter:
            payload["usage"] = {"include": True}
            if body.get("think"):
                payload["reasoning"] = {"enabled": True}
        _check_cancel(cancel)
        t0 = time.perf_counter()
        try:
            conn, resp = request(self.base_url + "/chat/completions", "POST", payload,
                                 {**self._headers(), "Accept": "text/event-stream"}, timeout=600)
        except (OSError, http.client.HTTPException) as exc:
            raise LLMError(f"model endpoint unreachable: {exc}") from exc
        if cancel is not None:
            cancel.attach(conn)
        try:
            if resp.status >= 400:
                raise LLMError(explain_http_error(resp.status, resp.read().decode(errors="replace")))
            calls: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] = {}
            for event in _sse_events(resp, cancel):
                if event.get("error"):
                    raise LLMError("provider error: " + json.dumps(event["error"])[:300])
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                    if reasoning:
                        yield {"message": {"role": "assistant", "thinking": reasoning}, "done": False}
                    if delta.get("content"):
                        yield {"message": {"role": "assistant", "content": delta["content"]}, "done": False}
                    for tc in delta.get("tool_calls") or []:
                        acc = calls.setdefault(int(tc.get("index", len(calls))), {"id": "", "name": "", "args": ""})
                        acc["id"] = tc.get("id") or acc["id"]
                        fn = tc.get("function") or {}
                        acc["name"] = fn.get("name") or acc["name"]
                        acc["args"] += fn.get("arguments") or ""
            if calls:
                out = []
                for i in sorted(calls):
                    c = calls[i]
                    try:
                        args = json.loads(c["args"] or "{}")
                    except ValueError:
                        args = {"_raw": c["args"]}
                    out.append({"id": c["id"] or f"call_{i}", "function": {"name": c["name"], "arguments": args}})
                yield {"message": {"role": "assistant", "content": "", "tool_calls": out}, "done": False}
            yield {"message": {"role": "assistant", "content": ""}, "done": True,
                   "prompt_eval_count": int(usage.get("prompt_tokens") or 0),
                   "eval_count": int(usage.get("completion_tokens") or 0),
                   "eval_duration": int((time.perf_counter() - t0) * 1e9), "cost": _float(usage.get("cost")),
                   "cached_tokens": int(usage.get("prompt_cache_hit_tokens") or 0)}
        except (OSError, http.client.HTTPException) as exc:
            if cancel is not None and getattr(cancel, "cancelled", False):
                raise Cancelled() from exc
            raise LLMError(f"lost the connection to the model: {exc}") from exc
        finally:
            if cancel is not None:
                cancel.attach(None)
            conn.close()


def _sse_events(resp: http.client.HTTPResponse, cancel: Any) -> Iterator[dict[str, Any]]:
    data: list[str] = []
    while True:
        line = resp.readline()
        _check_cancel(cancel)
        if not line:
            break
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if text.startswith(":"):          # keep-alive comment (": OPENROUTER PROCESSING")
            continue
        if text.startswith("data:"):
            data.append(text[5:].lstrip())
            continue
        if text == "" and data:
            payload = "\n".join(data)
            data = []
            if payload.strip() == "[DONE]":
                return
            try:
                yield json.loads(payload)
            except ValueError:
                continue
    if data and "\n".join(data).strip() not in ("", "[DONE]"):
        try:
            yield json.loads("\n".join(data))
        except ValueError:
            pass


def to_openai(messages: list[dict[str, Any]], reasoning_field: str = "") -> list[dict[str, Any]]:
    """Ollama-style messages (images as base64 list, tool_calls with dict arguments) -> OpenAI format.
    reasoning_field: for providers that need the model's earlier reasoning back on tool-call messages
    (DeepSeek: "reasoning_content"); others never see it."""
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id") or "", "content": m.get("content", "")})
            continue
        if role == "assistant" and m.get("tool_calls"):
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content") or None, "tool_calls": [
                {"id": c.get("id") or f"call_{i}", "type": "function",
                 "function": {"name": (c.get("function") or {}).get("name", ""),
                              "arguments": json.dumps((c.get("function") or {}).get("arguments") or {})}}
                for i, c in enumerate(m["tool_calls"])]}
            if reasoning_field:
                msg[reasoning_field] = m.get("thinking") or ""
            out.append(msg)
            continue
        if m.get("images"):
            parts: list[dict[str, Any]] = [{"type": "text", "text": m.get("content", "")}]
            for b64 in m["images"]:
                parts.append({"type": "image_url", "image_url": {"url": f"data:{_mime(b64)};base64,{b64}"}})
            out.append({"role": role, "content": parts})
            continue
        out.append({"role": role, "content": m.get("content", "")})
    return out


def _mime(b64: str) -> str:
    for prefix, mime in (("iVBOR", "image/png"), ("/9j/", "image/jpeg"), ("R0lGOD", "image/gif"), ("UklGR", "image/webp")):
        if b64.startswith(prefix):
            return mime
    return "image/jpeg"


def _float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _call(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    try:
        conn, resp = request(url, "POST", body, headers, timeout=timeout)
    except (OSError, http.client.HTTPException) as exc:
        raise LLMError(f"model endpoint unreachable: {exc}") from exc
    try:
        raw = resp.read().decode("utf-8", "replace")
    except (OSError, http.client.HTTPException) as exc:
        raise LLMError(f"model endpoint unreachable: {exc}") from exc
    finally:
        conn.close()
    if resp.status >= 400:
        raise LLMError(explain_http_error(resp.status, raw))
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise LLMError(f"model endpoint sent non-JSON: {raw[:200]}") from exc


def explain_http_error(status: int, raw: str) -> str:
    try:
        err = json.loads(raw).get("error")
        msg = err.get("message") if isinstance(err, dict) else err
    except (ValueError, AttributeError):
        msg = None
    msg = str(msg or raw[:300]).strip()
    hint = {401: " Check the API key on the Model connection tab.",
            402: " Your OpenRouter credit has run out.",
            404: " Check the model id in config/models.json.",
            429: " Rate limited: wait a moment or use another model."}.get(status, "")
    return f"HTTP {status}: {msg}{hint}"
