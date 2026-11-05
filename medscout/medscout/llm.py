"""Model clients. The LLM generates text; the runtime turns selected outputs into operations.

Providers
  mock    – deterministic scripted policy (mock_policy.py). Offline; for tests and demos.
            It reads ONLY the assembled context, like a real model would.
  ollama  – local model via Ollama /api/chat (Lec 4 practical). Uses `format`
            for JSON / JSON-schema constrained decoding (Lec 5 "Constraints during generation").
  openai  – any OpenAI-compatible chat-completions endpoint (OpenAI, HKU GenAI
            gateway if compatible, vLLM, LM Studio...). Azure-style also supported.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    model: str
    estimated_tokens: bool = False
    cost_usd: float | None = None   # set when the provider reports the cost (OpenRouter) or the router computes it
    route: str = ""                 # why the router chose this model (empty without the router)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def messages_tokens(messages: list[dict[str, str]]) -> int:
    return sum(estimate_tokens(m["content"]) + 4 for m in messages)


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        raise LLMError(f"HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"model endpoint unreachable: {exc}") from exc


class BaseLLM:
    name = "base"

    def __init__(self, model: str, temperature: float = 0.0, timeout_s: float = 120.0):
        self.model = model
        self.temperature = temperature
        self.timeout_s = timeout_s

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any] | None = None) -> LLMResult:
        raise NotImplementedError


class OllamaLLM(BaseLLM):
    name = "ollama"

    def complete(self, messages, schema=None):
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        # JSON-schema constrained decoding when the schema is simple enough; plain JSON mode otherwise.
        fmt: Any = schema if (schema and "oneOf" not in json.dumps(schema)) else "json"
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False, "format": fmt,
                                   "options": {"temperature": self.temperature,
                                               "num_ctx": int(os.environ.get("OLLAMA_NUM_CTX", "8192"))}}
        # Thinking models (qwen3.5, ...): MEDSCOUT_OLLAMA_THINK=true|false turns step-by-step reasoning
        # before each JSON decision on or off. Unset = the model's own default.
        think = os.environ.get("MEDSCOUT_OLLAMA_THINK", "").strip().lower()
        if think in ("true", "false"):
            payload["think"] = think == "true"
        t0 = time.perf_counter()
        data = _post(f"{host}/api/chat", payload, {}, self.timeout_s)
        text = data.get("message", {}).get("content", "")
        return LLMResult(text, int(data.get("prompt_eval_count", 0)), int(data.get("eval_count", 0)),
                         round((time.perf_counter() - t0) * 1000, 1), self.model)


class OpenAICompatLLM(BaseLLM):
    name = "openai"

    def complete(self, messages, schema=None):
        azure = os.environ.get("AZURE_OPENAI_ENDPOINT")
        if azure:
            ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
            url = f"{azure.rstrip('/')}/openai/deployments/{self.model}/chat/completions?api-version={ver}"
            headers = {"api-key": os.environ.get("AZURE_OPENAI_API_KEY", "")}
        else:
            base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions"
            headers = {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if os.environ.get("MEDSCOUT_JSON_MODE", "1") == "1":
            payload["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        data = _post(url, payload, headers, self.timeout_s)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise LLMError(f"unexpected response shape: {str(data)[:200]}") from exc
        usage = data.get("usage", {}) or {}
        est = not usage
        return LLMResult(text, int(usage.get("prompt_tokens", messages_tokens(messages))),
                         int(usage.get("completion_tokens", estimate_tokens(text))),
                         round((time.perf_counter() - t0) * 1000, 1), self.model, est)


class MockLLM(BaseLLM):
    name = "mock"

    def complete(self, messages, schema=None):
        from . import mock_policy
        t0 = time.perf_counter()
        text = mock_policy.respond(messages)
        return LLMResult(text, messages_tokens(messages), estimate_tokens(text),
                         round((time.perf_counter() - t0) * 1000, 2), self.model, True)


def make_llm(settings, private: bool = False) -> BaseLLM:
    """private: the run reads the user's own documents (they stay on local models unless allowed)."""
    if settings.provider == "router":
        from .models import shared_router
        from .models.routed import RoutedLLM
        return RoutedLLM(shared_router(settings), settings.default_model(), settings.temperature, private=private,
                         allow_cloud=settings.allow_cloud, max_cost=settings.limits.max_cost_usd)
    cls = {"mock": MockLLM, "ollama": OllamaLLM, "openai": OpenAICompatLLM}.get(settings.provider)
    if cls is None:
        raise ValueError(f"unknown provider {settings.provider!r}; use mock | ollama | openai | router")
    return cls(settings.default_model(), settings.temperature)


def call_model(llm: BaseLLM, messages: list[dict[str, str]], *, state, tracer, settings, purpose: str,
               task_id: str = "", step: int = 0, schema: dict[str, Any] | None = None,
               retries: int = 1) -> LLMResult:
    """One accounted model call: tokens, latency, cost, trace. Bounded retry on endpoint errors."""
    ctx_tokens = messages_tokens(messages)
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if getattr(llm, "routed", False):
                res = llm.complete(messages, schema, purpose=purpose, task_id=task_id, spent=state.counters.cost_usd)
            else:
                res = llm.complete(messages, schema)
            break
        except LLMError as exc:
            last = exc
            tracer.event("model_error", purpose=purpose, task_id=task_id, attempt=attempt + 1, error=str(exc))
            if getattr(exc, "final", False):        # cost limit / privacy: retrying cannot help
                raise
            time.sleep(1.0 * (attempt + 1))
    else:
        raise LLMError(str(last))
    c = state.counters
    c.model_calls += 1
    c.tokens_in += res.tokens_in
    c.tokens_out += res.tokens_out
    cost = res.cost_usd if res.cost_usd is not None else \
        (res.tokens_in * settings.price_in_per_m + res.tokens_out * settings.price_out_per_m) / 1e6
    c.cost_usd = round(c.cost_usd + cost, 6)
    tracer.event("model_call", purpose=purpose, task_id=task_id, step=step, model=res.model,
                 context_tokens=ctx_tokens, tokens_in=res.tokens_in, tokens_out=res.tokens_out,
                 estimated_tokens=res.estimated_tokens, latency_ms=res.latency_ms, cost_usd=round(cost, 6),
                 route=res.route,
                 output=res.text[:2000])
    return res
