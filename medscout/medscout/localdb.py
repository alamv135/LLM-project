"""Read-only access to the user's own SQLite database, for the AI (Ask chat and research runs).

Safety, in layers (the model writes the SQL, so it is treated as untrusted):
  1. the file is opened read-only (sqlite URI mode=ro) and PRAGMA query_only is on;
  2. an authorizer allows only reading (SELECT, reading columns, functions, WITH RECURSIVE) and a
     few schema PRAGMAs; INSERT/UPDATE/DELETE/CREATE/DROP/ATTACH and everything else are refused;
  3. one statement per call; a time limit (progress handler) and a row limit;
  4. long text cells are shortened before the model sees them.
Database contents are private: the router keeps them on local models unless the user allows cloud.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

MAX_ROWS = 200          # rows a query may return
MODEL_ROWS = 50         # rows shown to the model
CELL_CHARS = 300
ALLOWED_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info", "foreign_key_list", "table_list"}


class DatabaseError(Exception):
    pass


class LocalDB:
    def __init__(self, path: str | Path, timeout_s: float = 5.0):
        self.path = Path(os.path.expanduser(str(path))).resolve()
        if not self.path.is_file():
            raise DatabaseError(f"No database file at {self.path}")
        self.timeout_s = timeout_s
        self._lock = threading.Lock()
        self._deadline = 0.0
        uri = "file:" + urllib.parse.quote(str(self.path)) + "?mode=ro"
        try:
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self.conn.execute("PRAGMA query_only = ON")
            self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.DatabaseError as exc:
            raise DatabaseError(f"{self.path.name} is not a readable SQLite database: {exc}") from exc
        self.conn.set_authorizer(self._authorize)
        self.conn.set_progress_handler(self._progress, 2000)

    @property
    def name(self) -> str:
        return self.path.name

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ----- guards -----------------------------------------------------------------------------------
    @staticmethod
    def _authorize(action: int, arg1: Any, arg2: Any, dbname: Any, source: Any) -> int:
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                      getattr(sqlite3, "SQLITE_RECURSIVE", 33)):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in ALLOWED_PRAGMAS:      # all read-only
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def _progress(self) -> int:
        return 1 if self._deadline and time.monotonic() > self._deadline else 0

    def _run(self, sql: str, params: tuple = ()) -> tuple[list[str], list[tuple], bool]:
        with self._lock:
            self._deadline = time.monotonic() + self.timeout_s
            try:
                cur = self.conn.execute(sql, params)
                rows = cur.fetchmany(MAX_ROWS + 1)
                cols = [d[0] for d in cur.description or []]
            except sqlite3.OperationalError as exc:
                msg = str(exc)
                if "interrupted" in msg:
                    raise DatabaseError(f"query stopped after {self.timeout_s:.0f} s; add a WHERE or LIMIT") from exc
                if "not authorized" in msg:
                    raise DatabaseError("only reading (SELECT) is allowed") from exc
                raise DatabaseError(msg) from exc
            except (sqlite3.Warning, sqlite3.ProgrammingError) as exc:
                raise DatabaseError(f"one SELECT statement at a time ({exc})") from exc
            except sqlite3.DatabaseError as exc:
                raise DatabaseError(str(exc)) from exc
            finally:
                self._deadline = 0.0
        return cols, rows[:MAX_ROWS], len(rows) > MAX_ROWS

    # ----- what the AI can do -----------------------------------------------------------------------
    def tables(self) -> list[dict[str, Any]]:
        _, rows, _ = self._run("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
                               "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        out = []
        for name, kind in rows:
            cols = [r[1] for r in self._run(f'PRAGMA table_info("{_ident(name)}")')[1]]
            try:
                count = self._run(f'SELECT count(*) FROM "{_ident(name)}"')[1][0][0]
            except DatabaseError:
                count = None
            out.append({"name": name, "type": kind, "rows": count, "columns": cols})
        return out

    def describe(self, table: str) -> dict[str, Any]:
        names = {t["name"].lower(): t for t in self.tables()}
        t = names.get((table or "").strip().strip('"').lower())
        if t is None:
            raise DatabaseError(f"no table {table!r}; tables: {', '.join(n['name'] for n in names.values())}")
        q = _ident(t["name"])
        cols = [{"name": r[1], "type": r[2], "not_null": bool(r[3]), "primary_key": bool(r[5])}
                for r in self._run(f'PRAGMA table_info("{q}")')[1]]
        fks = [{"column": r[3], "references": f"{r[2]}.{r[4]}"} for r in self._run(f'PRAGMA foreign_key_list("{q}")')[1]]
        names_, sample, _ = self._run(f'SELECT * FROM "{q}" LIMIT 3')
        return {"name": t["name"], "type": t["type"], "rows": t["rows"], "columns": cols, "foreign_keys": fks,
                "sample": [dict(zip(names_, map(_cell, r))) for r in sample]}

    def query(self, sql: str) -> dict[str, Any]:
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            raise DatabaseError("empty query")
        if not re.match(r"(?is)^\s*(select|with)\b", sql):
            raise DatabaseError("only SELECT queries (or WITH ... SELECT) are allowed")
        cols, rows, truncated = self._run(sql)
        return {"columns": cols, "rows": [[_cell(v) for v in r] for r in rows], "row_count": len(rows),
                "truncated": truncated}


def _ident(name: str) -> str:
    return str(name).replace('"', '""')


def _cell(v: Any) -> Any:
    if isinstance(v, bytes):
        return f"<{len(v)} bytes>"
    if isinstance(v, str) and len(v) > CELL_CHARS:
        return v[:CELL_CHARS] + "…"
    return v


def as_markdown(result: dict[str, Any], max_rows: int = MODEL_ROWS) -> str:
    """Query result as a Markdown table for the model."""
    cols, rows = result["columns"], result["rows"]
    if not cols:
        return "(no columns)"
    if not rows:
        return "| " + " | ".join(cols) + " |\n(no rows)"
    def esc(v: Any) -> str:
        return ("NULL" if v is None else str(v)).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows[:max_rows]]
    more = len(rows) - max_rows
    if more > 0 or result.get("truncated"):
        lines.append(f"(showing {min(len(rows), max_rows)} of {len(rows)}{'+' if result.get('truncated') else ''} rows; "
                     "use WHERE, GROUP BY or LIMIT to narrow it)")
    return "\n".join(lines)


def schema_text(db: LocalDB, max_tables: int = 40) -> str:
    """A short overview (tables, row counts, columns) for prompts."""
    parts = []
    for t in db.tables()[:max_tables]:
        parts.append(f"- {t['name']} ({t['rows'] if t['rows'] is not None else '?'} rows): {', '.join(t['columns'][:30])}")
    return "\n".join(parts)
