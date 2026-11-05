"""Settings for the Ask area: defaults and loading/saving <runs_dir>/ask/settings.json.

Ask stores everything (settings, chats, uploaded files) in one folder, `runs/ask/` by default,
which git ignores. The Ollama address follows MedScout's "Model connection" tab (OLLAMA_HOST)
unless a different address is set here.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

STATIC_DIR = Path(__file__).resolve().parent / "static"   # the chat page files

DEFAULT_MODEL = "auto"          # the router: local models first, API models only when needed
CONTEXT_CHOICES = [8192, 16384, 32768, 65536, 131072]


def normalize_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if "://" not in raw:
        raw = "http://" + raw
    u = urlparse(raw)
    host = u.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::"):          # "listen everywhere" means "talk to this machine"
        host = "127.0.0.1"
    if ":" in host:
        host = "[" + host + "]"
    port = u.port or (443 if u.scheme == "https" else 11434)
    return "%s://%s:%d" % (u.scheme or "http", host, port)


DEFAULTS: Dict[str, Any] = {
    "model": DEFAULT_MODEL,
    "ollama_url": "",            # empty = MedScout's connection (OLLAMA_HOST), else http://127.0.0.1:11434
    "num_ctx": 32768,            # context window in tokens (how much the model can "see" at once)
    "temperature": None,         # None = use the model's own recommended value
    "think": False,              # let the model reason step by step before answering (slower)
    "web_search": True,          # let the model search the web / read pages
    "medtech": True,             # let the model use MedScout's openFDA / ClinicalTrials.gov / PubMed tools
    "medtech_backend": "live",   # "live" (the real APIs) or a demo case name (offline synthetic mirror)
    "database": False,           # let the model read your SQLite database (set up in the console)
    "max_cost_per_answer": 0.10, # USD an answer may spend on API models (0 = no limit)
    "search_backend": "auto",    # auto | duckduckgo | ollama
    "ollama_api_key": "",        # only for Ollama's web search API (optional)
    "custom_instructions": "",
    "keep_alive": "30m",         # how long Ollama keeps the model in memory after a reply
    "max_tool_rounds": 6,        # at most this many search/read rounds per answer
    "theme": "system",           # system | light | dark
}


class Settings:
    """Thread-safe settings stored in <data_dir>/settings.json."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "settings.json"
        self._lock = threading.Lock()
        self._values = dict(DEFAULTS)
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                for key, value in saved.items():
                    if key in DEFAULTS:
                        self._values[key] = value
            except (OSError, ValueError):
                pass  # a broken settings file falls back to the defaults

    def get(self, key: str) -> Any:
        with self._lock:
            return self._values.get(key, DEFAULTS.get(key))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def ollama_url(self) -> str:
        """The address to use now: this setting, else MedScout's connection, else the default."""
        own = self.get("ollama_url")
        if own:
            return own
        env = (os.environ.get("OLLAMA_HOST") or "").strip()
        return normalize_url(env) if env else "http://127.0.0.1:11434"

    def api_key(self) -> str:
        """Ollama API key for web search: the settings value, else the OLLAMA_API_KEY variable."""
        return (self.get("ollama_api_key") or os.environ.get("OLLAMA_API_KEY") or "").strip()

    def public(self) -> Dict[str, Any]:
        """Settings for the browser. The API key itself is never sent back."""
        values = self.snapshot()
        values["ollama_api_key"] = ""
        values["ollama_api_key_set"] = bool(self.api_key())
        values["ollama_api_key_from_env"] = bool(os.environ.get("OLLAMA_API_KEY")) and not self.get("ollama_api_key")
        values["ollama_url_effective"] = self.ollama_url()
        values["data_dir"] = str(self.path.parent)
        return values

    def update(self, changes: Dict[str, Any]) -> None:
        clean: Dict[str, Any] = {}
        for key, value in changes.items():
            if key not in DEFAULTS:
                continue
            if key == "ollama_api_key":
                if not value:
                    continue          # empty field = keep the saved key
                value = str(value).strip()
            elif key == "ollama_url":
                value = normalize_url(str(value)) if str(value or "").strip() else ""
            elif key == "num_ctx":
                value = max(2048, min(262144, int(value)))
            elif key == "temperature":
                value = None if value in (None, "") else max(0.0, min(2.0, float(value)))
            elif key == "max_tool_rounds":
                value = max(1, min(12, int(value)))
            elif key == "max_cost_per_answer":
                value = max(0.0, min(100.0, float(value or 0)))
            elif key in ("think", "web_search", "medtech", "database"):
                value = bool(value)
            elif key == "medtech_backend":
                value = str(value or "live").strip()
                if value != "live" and not re.fullmatch(r"[a-z0-9_-]{1,40}", value):
                    continue
            elif key == "search_backend" and value not in ("auto", "duckduckgo", "ollama"):
                continue
            elif key == "theme" and value not in ("system", "light", "dark"):
                continue
            elif key in ("model", "keep_alive", "custom_instructions"):
                value = str(value or "").strip() or DEFAULTS[key]
            clean[key] = value
        if changes.get("ollama_api_key_clear"):
            clean["ollama_api_key"] = ""
        with self._lock:
            self._values.update(clean)
            self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._values, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)    # the file may hold an API key: only you can read it
        except OSError:
            pass
