"""Personal settings shared by runs and Ask, stored in <runs_dir>/settings.json (git-ignored):
the path of your own SQLite database and a note on what is in it. MEDSCOUT_DATA_DB overrides the path."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

DEFAULTS = {"database_path": "", "database_notes": ""}
_lock = threading.Lock()


def _file(runs_dir: Path) -> Path:
    return Path(runs_dir) / "settings.json"


def load(runs_dir: Path) -> dict[str, Any]:
    out = dict(DEFAULTS)
    try:
        out.update({k: v for k, v in json.loads(_file(runs_dir).read_text()).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if os.environ.get("MEDSCOUT_DATA_DB"):
        out["database_path"] = os.environ["MEDSCOUT_DATA_DB"]
    return out


def save(runs_dir: Path, changes: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        cur = load(runs_dir)
        for k in DEFAULTS:
            if k in changes:
                cur[k] = str(changes[k] or "").strip()
        f = _file(runs_dir)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cur, indent=2))
        return cur


def open_database(runs_dir: Path):
    """The configured LocalDB, or None when no database is set."""
    from .localdb import LocalDB
    path = load(runs_dir)["database_path"]
    return LocalDB(path) if path else None
