"""The model list (config/models.json) plus what we learn about each model at run time.

A model has a short alias ("claude", "qwen-local"), a provider (ollama / openrouter) and the
provider's id. Prices, context size and abilities (tools, images) start from the file and are
refreshed from OpenRouter's public model list (cached for a day) and from Ollama's /api/show.

Names that are not aliases also work: "ollama:llama3.2:3b" or any tag with ":" is a local Ollama
model; "openrouter:vendor/model" or "vendor/model" is an OpenRouter model.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import ROOT
from .http import get_json, normalize_url

CONFIG_PATH = ROOT / "config" / "models.json"
CATALOG_TTL_S = 24 * 3600


@dataclass
class ModelInfo:
    alias: str
    provider: str
    kind: str                      # "ollama" | "openai"
    id: str
    label: str = ""
    tier: int = 0                  # 0 local / free, 1 cheap API, 2 premium API
    local: bool = True
    price_in: float = 0.0          # USD per 1M input tokens
    price_out: float = 0.0         # USD per 1M output tokens
    context: int = 8192
    tools: bool = True
    vision: bool = False
    thinking: bool = False
    json_schema: bool = False      # provider enforces a JSON schema (structured outputs)
    notes: list[str] = field(default_factory=list)

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6

    def view(self) -> dict[str, Any]:
        return asdict(self)


class Registry:
    def __init__(self, path: Path = CONFIG_PATH, cache_dir: Path | None = None):
        self.path = Path(path)
        self.cache_path = (Path(cache_dir) if cache_dir else ROOT / "runs") / "openrouter_catalog.json"
        self._lock = threading.Lock()
        self.local_status: dict[str, dict[str, Any]] = {}   # ollama id -> {installed, caps...}
        self.catalog_info = ""
        self.reload()

    # ----- loading --------------------------------------------------------------------------------
    def reload(self) -> None:
        cfg = json.loads(self.path.read_text())
        providers = cfg.get("providers") or {}
        models: dict[str, ModelInfo] = {}
        for alias, m in (cfg.get("models") or {}).items():
            prov = providers.get(m.get("provider", ""))
            if prov is None:
                raise ValueError(f"model {alias!r}: unknown provider {m.get('provider')!r}")
            kind = prov.get("kind", "openai")
            models[alias] = ModelInfo(
                alias=alias, provider=m["provider"], kind=kind, id=str(m["id"]), label=m.get("label") or m["id"],
                tier=int(m.get("tier", 0 if kind == "ollama" else 1)), local=kind == "ollama",
                price_in=float(m.get("price_in", 0)), price_out=float(m.get("price_out", 0)),
                context=int(m.get("context", 8192)), tools=bool(m.get("tools", True)),
                vision=bool(m.get("vision", False)), thinking=bool(m.get("thinking", False)),
                json_schema=bool(m.get("json_schema", kind == "ollama")))
        with self._lock:
            self.cfg, self.providers, self.models = cfg, providers, models
            self.router_cfg = cfg.get("router") or {}
        self._apply_cached_catalog()

    # ----- providers ------------------------------------------------------------------------------
    def base_url(self, provider: str) -> str:
        p = self.providers.get(provider) or {}
        env = os.environ.get(p.get("base_url_env", "") or "_none_", "").strip()
        if p.get("kind") == "ollama":
            return normalize_url(env or p.get("base_url") or "http://127.0.0.1:11434", 11434)
        return (env or p.get("base_url", "")).rstrip("/")

    def api_key(self, provider: str) -> str:
        p = self.providers.get(provider) or {}
        return os.environ.get(p.get("key_env", "") or "_none_", "").strip()

    def first_provider(self, kind: str) -> str:
        return next((n for n, p in self.providers.items() if p.get("kind") == kind), "")

    # ----- lookup ---------------------------------------------------------------------------------
    def get(self, name: str) -> ModelInfo:
        name = (name or "").strip()
        if name in self.models:
            return self.models[name]
        for m in self.models.values():            # the provider id also works ("qwen3.5:9b")
            if m.id == name:
                return m
        prefix, _, rest = name.partition(":")
        p = self.providers.get(prefix)
        if p and rest and p.get("kind") == "openai":            # e.g. "deepseek:deepseek-v4-pro"
            m = ModelInfo(alias=name, provider=prefix, kind="openai", id=rest, label=rest, tier=1, local=False)
            if p.get("openrouter"):
                self._fill_from_catalog(m, self._read_cache().get("models", {}))
            return m
        if name.startswith("ollama:") or (":" in name and "/" not in name):
            tag = name.split("ollama:", 1)[-1]
            return ModelInfo(alias=tag, provider=self.first_provider("ollama"), kind="ollama", id=tag, label=tag,
                             tier=0, local=True, json_schema=True)
        if "/" in name:
            mid = name
            prov = next((n for n, q in self.providers.items() if q.get("openrouter")), self.first_provider("openai"))
            m = ModelInfo(alias=mid, provider=prov, kind="openai", id=mid, label=mid, tier=1, local=False)
            self._fill_from_catalog(m, self._read_cache().get("models", {}))
            return m
        raise KeyError(f"unknown model {name!r}; use an alias from config/models.json, an Ollama tag "
                       "(e.g. qwen3.5:9b) or an OpenRouter id (vendor/model)")

    def all(self) -> list[ModelInfo]:
        return sorted(self.models.values(), key=lambda m: (m.tier, m.alias))

    def available(self, m: ModelInfo) -> tuple[bool, str]:
        """Can this model be called right now?"""
        if not m.local:
            if (self.providers.get(m.provider) or {}).get("key_env") and not self.api_key(m.provider):
                return False, "no API key (set it on the Model connection tab)"
            return True, ""
        st = self.local_status.get(m.id)
        if st is not None and not st.get("installed"):
            return False, "not downloaded in Ollama (ollama pull %s)" % m.id
        return True, ""

    # ----- live information -----------------------------------------------------------------------
    def refresh_local(self, timeout: float = 5.0) -> str:
        """Ask Ollama which models are installed and what each can do."""
        from ..ask.ollama_client import OllamaClient, OllamaError
        prov = self.first_provider("ollama")
        if not prov:
            return "no Ollama provider configured"
        client = OllamaClient(self.base_url(prov), timeout=timeout)
        try:
            installed = {m.get("name") for m in client.models()}
        except OllamaError as exc:
            self.local_status = {}
            return str(exc)
        status: dict[str, dict[str, Any]] = {}
        for m in self.models.values():
            if not m.local:
                continue
            ok = m.id in installed or (m.id + ":latest") in installed
            status[m.id] = {"installed": ok}
            if ok:
                try:
                    info = client.show(m.id)
                    caps = info.get("capabilities") or []
                    m.tools, m.vision, m.thinking = "tools" in caps, "vision" in caps, "thinking" in caps
                    if info.get("context_length"):
                        m.context = int(info["context_length"])
                except OllamaError:
                    pass
        self.local_status = status
        return "Ollama: %d of %d local models downloaded" % (sum(s["installed"] for s in status.values()), len(status))

    def refresh_catalog(self, force: bool = False, timeout: float = 15.0) -> str:
        """Fetch OpenRouter's model list (prices, context, abilities) and apply it."""
        cache = self._read_cache()
        if not force and cache and time.time() - cache.get("fetched", 0) < CATALOG_TTL_S:
            self._apply(cache)
            return self.catalog_info
        prov = next((n for n, p in self.providers.items() if p.get("openrouter")), "")
        if not prov:
            return "no OpenRouter provider configured"
        try:
            data = get_json(self.base_url(prov) + "/models", timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - keep the prices from the file
            self.catalog_info = f"OpenRouter model list not reachable ({exc}); using prices from models.json"
            return self.catalog_info
        models = {}
        for item in data.get("data") or []:
            models[item.get("id", "")] = {
                "price_in": _per_million(((item.get("pricing") or {}).get("prompt"))),
                "price_out": _per_million(((item.get("pricing") or {}).get("completion"))),
                "context": item.get("context_length") or 0,
                "params": item.get("supported_parameters") or [],
                "inputs": ((item.get("architecture") or {}).get("input_modalities")) or [],
                "name": item.get("name", ""),
            }
        cache = {"fetched": time.time(), "models": models}
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(cache))
        except OSError:
            pass
        self._apply(cache)
        return self.catalog_info

    def _read_cache(self) -> dict[str, Any]:
        try:
            return json.loads(self.cache_path.read_text())
        except (OSError, ValueError):
            return {}

    def _apply_cached_catalog(self) -> None:
        cache = self._read_cache()
        if cache:
            self._apply(cache)

    def _apply(self, cache: dict[str, Any]) -> None:
        found = 0
        for m in self.models.values():
            if self._is_openrouter(m):
                found += self._fill_from_catalog(m, cache.get("models", {}))
        cloud = sum(1 for m in self.models.values() if self._is_openrouter(m))
        age = (time.time() - cache.get("fetched", time.time())) / 3600
        self.catalog_info = f"OpenRouter list: {found} of {cloud} cloud models found (updated {age:.0f} h ago)"

    def _is_openrouter(self, m: ModelInfo) -> bool:
        return not m.local and bool((self.providers.get(m.provider) or {}).get("openrouter"))

    def check_direct_providers(self, timeout: float = 10.0) -> list[str]:
        """For direct APIs (e.g. DeepSeek) with a key: does the provider still offer each model id?"""
        out = []
        for name, p in self.providers.items():
            if p.get("kind") != "openai" or p.get("openrouter") or not self.api_key(name):
                continue
            try:
                data = get_json(self.base_url(name) + "/models", {"Authorization": "Bearer " + self.api_key(name)}, timeout)
            except Exception as exc:  # noqa: BLE001
                out.append(f"{name}: model list not reachable ({str(exc)[:80]})")
                continue
            ids = {d.get("id") for d in data.get("data") or []}
            for m in self.models.values():
                if m.provider == name:
                    m.notes = [n for n in m.notes if not n.startswith("not offered")]
                    if ids and m.id not in ids:
                        m.notes.append(f"not offered by {name} - available: {', '.join(sorted(ids))[:120]}")
            out.append(f"{name}: {len(ids)} models offered")
        return out

    @staticmethod
    def _fill_from_catalog(m: ModelInfo, catalog: dict[str, Any]) -> int:
        c = catalog.get(m.id)
        m.notes = [n for n in m.notes if not n.startswith("not in OpenRouter")]
        if not c:
            if catalog:
                m.notes.append("not in OpenRouter's model list - check the id")
            return 0
        if c["price_in"] is not None:
            m.price_in = c["price_in"]
        if c["price_out"] is not None:
            m.price_out = c["price_out"]
        if c["context"]:
            m.context = int(c["context"])
        if c["params"]:
            m.tools = "tools" in c["params"]
            m.json_schema = "structured_outputs" in c["params"]
            m.thinking = "reasoning" in c["params"] or "include_reasoning" in c["params"]
        if c["inputs"]:
            m.vision = "image" in c["inputs"]
        return 1


def _per_million(value: Any) -> float | None:
    try:
        return round(float(value) * 1e6, 6)
    except (TypeError, ValueError):
        return None
