"""Offline "public mirror" backend.

A case folder may contain data/cases/<case>/public_mirror/<source>.json with
records in the *raw* format of the real API (openFDA, ClinicalTrials.gov v2,
PubMed esummary). The same normalisers run on mirror and live data, so the
mirror exercises the real parsing path.

Mirror files shipped with the demo cases are SYNTHETIC (fictional companies)
and every evidence item from them is labelled synthetic in the brief.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import ToolError

_WORD = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "and", "or", "for", "in", "on", "with", "to", "by", "is", "are", "inc", "llc", "ltd", "corp"}


def words(text: str) -> list[str]:
    return [w for w in _WORD.findall((text or "").lower()) if w not in STOP]


def load(mirror_dir: Path | None, name: str) -> tuple[list[dict[str, Any]], bool]:
    """Return (records, synthetic_flag)."""
    if mirror_dir is None:
        raise ToolError("unavailable", "no offline mirror configured for this case; use --backend live")
    path = mirror_dir / f"{name}.json"
    if not path.exists():
        raise ToolError("unavailable", f"offline mirror has no '{name}' data; use --backend live")
    data = json.loads(path.read_text())
    return data.get("records", []), bool(data.get("synthetic", False))


def field_match(value: str, query: str) -> bool:
    """All query words appear in the field (case/punctuation-insensitive)."""
    have = set(words(value))
    need = words(query)
    return bool(need) and all(w in have for w in need)


def text_score(text: str, query: str) -> float:
    """Fraction of query words present in text (cheap lexical relevance)."""
    need = set(words(query))
    if not need:
        return 0.0
    have = set(words(text))
    return len(need & have) / len(need)
