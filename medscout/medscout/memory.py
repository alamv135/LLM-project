"""Persistent memory and checkpoints (SQLite).

Lec 4 "Memory categories / scope and storage":
  semantic   – entity aliases confirmed by a human (company <-> FDA applicant name)
  episodic   – summaries of completed diligence runs (reusable past cases)
  checkpoint – one RunState snapshot per run, written after every node,
               so an interrupted run can resume (like a LangGraph checkpointer)
  http_cache – fetched public records with timestamps (freshness / refresh)

Write lifecycle: observation -> candidate -> validate (human confirmation)
-> scoped record with source and update time.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS aliases (
  company TEXT NOT NULL, alias TEXT NOT NULL, source TEXT, confirmed_by TEXT,
  updated_at TEXT, PRIMARY KEY (company, alias));
CREATE TABLE IF NOT EXISTS cases (
  run_id TEXT PRIMARY KEY, company TEXT, created_at TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS checkpoints (
  run_id TEXT PRIMARY KEY, phase TEXT, updated_at TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS http_cache (
  url TEXT PRIMARY KEY, fetched_at REAL, body TEXT);
"""


def norm_company(name: str) -> str:
    return " ".join(name.lower().replace(",", " ").replace(".", " ").split())


class Memory:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _exec(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            cur = self.conn.execute(sql, args)
            rows = cur.fetchall()
            self.conn.commit()
            return rows

    # ------------------------------------------------------------ semantic memory
    def aliases(self, company: str) -> list[str]:
        rows = self._exec("SELECT alias FROM aliases WHERE company=? ORDER BY alias", (norm_company(company),))
        return [r[0] for r in rows]

    def add_alias(self, company: str, alias: str, source: str, confirmed_by: str) -> None:
        if not alias.strip() or norm_company(alias) == norm_company(company):
            return  # validate / deduplicate before writing
        self._exec("INSERT OR REPLACE INTO aliases VALUES (?,?,?,?,?)",
                   (norm_company(company), alias.strip(), source, confirmed_by,
                    time.strftime("%Y-%m-%d")))

    # ------------------------------------------------------------ episodic memory
    def record_case(self, run_id: str, company: str, summary: dict[str, Any]) -> None:
        self._exec("INSERT OR REPLACE INTO cases VALUES (?,?,?,?)",
                   (run_id, norm_company(company), time.strftime("%Y-%m-%d"), json.dumps(summary)))

    def past_cases(self, company: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = self._exec("SELECT run_id, created_at, summary FROM cases WHERE company=? "
                          "ORDER BY created_at DESC LIMIT ?", (norm_company(company), limit))
        return [{"run_id": r[0], "date": r[1], **json.loads(r[2])} for r in rows]

    # ------------------------------------------------------------ checkpoints
    def save_checkpoint(self, run_id: str, phase: str, state_json: str) -> None:
        self._exec("INSERT OR REPLACE INTO checkpoints VALUES (?,?,?,?)",
                   (run_id, phase, time.strftime("%Y-%m-%dT%H:%M:%S"), state_json))

    def load_checkpoint(self, run_id: str) -> tuple[str, str] | None:
        rows = self._exec("SELECT phase, state FROM checkpoints WHERE run_id=?", (run_id,))
        return (rows[0][0], rows[0][1]) if rows else None

    def latest_run_id(self) -> str | None:
        rows = self._exec("SELECT run_id FROM checkpoints ORDER BY updated_at DESC LIMIT 1")
        return rows[0][0] if rows else None

    # ------------------------------------------------------------ HTTP cache
    def http_get(self, url: str, max_age_s: float) -> dict[str, Any] | None:
        rows = self._exec("SELECT fetched_at, body FROM http_cache WHERE url=?", (url,))
        if rows and time.time() - rows[0][0] <= max_age_s:
            return json.loads(rows[0][1])
        return None

    def http_put(self, url: str, data: dict[str, Any]) -> None:
        self._exec("INSERT OR REPLACE INTO http_cache VALUES (?,?,?)", (url, time.time(), json.dumps(data)))
