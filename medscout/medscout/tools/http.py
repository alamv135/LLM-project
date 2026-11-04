"""Minimal HTTP JSON client for live public sources (stdlib only).

- Timeout on every request (bounded wait).
- Classifies failures for the gateway's retry policy (Lec 5 "Fault tolerance
  in the runtime": transient vs permanent).
- Optional read-through cache with a freshness TTL (Lec 4 "Context
  engineering: Refresh — re-query operational data when it expires").
- Optional fixture recording (MEDSCOUT_RECORD_DIR) so live runs can be
  replayed for reproducible evaluation.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .base import ToolError

USER_AGENT = "MedScout/0.1 (COMP7608B student project; research use)"
CACHE_TTL_S = float(os.environ.get("MEDSCOUT_CACHE_TTL_S", str(24 * 3600)))


def build_url(base: str, params: dict[str, Any]) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return base + ("?" + urllib.parse.urlencode(clean) if clean else "")


def get_json(url: str, timeout_s: float, cache: Any = None, not_found_ok: bool = False) -> dict[str, Any]:
    """GET a JSON document. `not_found_ok`: map HTTP 404 to {} (openFDA uses 404 for 'no matches')."""
    if cache is not None:
        hit = cache.http_get(url, max_age_s=CACHE_TTL_S)
        if hit is not None:
            return hit
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and not_found_ok:
            return {}
        if exc.code == 429 or exc.code >= 500:
            raise ToolError("transient", f"HTTP {exc.code} from {urllib.parse.urlparse(url).netloc}") from exc
        if exc.code == 403:
            raise ToolError("unavailable", f"HTTP 403 (access denied) from {urllib.parse.urlparse(url).netloc}") from exc
        raise ToolError("permanent", f"HTTP {exc.code} from {urllib.parse.urlparse(url).netloc}") from exc
    except TimeoutError as exc:
        raise ToolError("transient", f"timeout after {timeout_s}s") from exc
    except urllib.error.URLError as exc:
        reason = str(exc.reason)
        kind = "transient" if "timed out" in reason else "unavailable"
        raise ToolError(kind, f"cannot reach {urllib.parse.urlparse(url).netloc}: {reason}") from exc
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ToolError("permanent", "response was not JSON") from exc
    if cache is not None:
        cache.http_put(url, data)
    _maybe_record(url, data)
    return data


def _maybe_record(url: str, data: dict[str, Any]) -> None:
    rec_dir = os.environ.get("MEDSCOUT_RECORD_DIR")
    if not rec_dir:
        return
    p = Path(rec_dir)
    p.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode()).hexdigest()[:16]
    (p / f"{key}.json").write_text(json.dumps({"url": url, "response": data}, indent=1))
