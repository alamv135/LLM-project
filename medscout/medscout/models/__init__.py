"""Models: one list of models (config/models.json), two providers (Ollama, OpenAI-compatible such as
OpenRouter) and a router that picks a model per call. See registry.py, providers.py, router.py."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from .registry import CONFIG_PATH, ModelInfo, Registry
from .router import BudgetExceeded, Decision, Needs, PrivacyBlocked, Router

_lock = threading.Lock()
_shared: dict[str, object] = {}


def shared_router(settings=None) -> Router:
    """One registry/router per process, reloaded when config/models.json changes.
    MEDSCOUT_MODELS=path uses another model list (tests, experiments)."""
    cache_dir = getattr(settings, "runs_dir", None)
    path = Path(os.environ.get("MEDSCOUT_MODELS") or CONFIG_PATH)
    with _lock:
        mtime = path.stat().st_mtime if path.exists() else 0
        router = _shared.get("router")
        key = (str(path), mtime, str(cache_dir))
        if router is None or _shared.get("key") != key:
            reg = Registry(path, cache_dir)
            router = Router(reg)
            _shared.update(router=router, key=key)
        return router  # type: ignore[return-value]


__all__ = ["BudgetExceeded", "Decision", "ModelInfo", "Needs", "PrivacyBlocked", "Registry", "Router",
           "shared_router"]
