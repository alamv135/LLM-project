"""Your own SQLite database as research tools (only when "Let the agent query my database" is on).

Results are registered as evidence with source "Local database" and independent=False: it is the
user's internal data, useful context but never independent public support for a claim.
"""

from __future__ import annotations

import hashlib
from typing import Any

from ..localdb import DatabaseError
from .base import ToolContext, ToolError, envelope, string_arg

LIST_INPUT = {"type": "object", "additionalProperties": False, "properties": {}}
DESCRIBE_INPUT = {"type": "object", "additionalProperties": False, "required": ["table"],
                  "properties": {"table": string_arg("Table or view name")}}
QUERY_INPUT = {"type": "object", "additionalProperties": False, "required": ["sql"],
               "properties": {"sql": {"type": "string", "minLength": 6, "maxLength": 4000,
                                      "description": "One read-only SQLite SELECT statement"}}}
OUTPUT = envelope({"type": "object", "required": ["_row"]})


def _db(ctx: ToolContext):
    if ctx.database is None:
        raise ToolError("unavailable", "no database is connected (Models & data tab)")
    return ctx.database


def db_list_tables(ctx: ToolContext) -> dict[str, Any]:
    db = _db(ctx)
    try:
        tables = db.tables()
    except DatabaseError as exc:
        raise ToolError("permanent", str(exc)) from exc
    recs = [{"_row": f"table:{t['name']}", "_db": db.name, "table": t["name"], "type": t["type"], "rows": t["rows"],
             "columns": ", ".join(t["columns"])} for t in tables]
    note = f"Database {db.name}." + (f" Owner's note: {ctx.database_notes}" if ctx.database_notes else "")
    return {"source": "local_database", "total": len(recs), "records": recs, "note": note}


def db_describe_table(ctx: ToolContext, table: str) -> dict[str, Any]:
    db = _db(ctx)
    try:
        d = db.describe(table)
    except DatabaseError as exc:
        raise ToolError("permanent", str(exc)) from exc
    cols = ", ".join(f"{c['name']} {c['type']}{' PK' if c['primary_key'] else ''}" for c in d["columns"])
    rec = {"_row": f"schema:{d['name']}", "_db": db.name, "table": d["name"], "rows": d["rows"], "columns": cols,
           "foreign_keys": ", ".join(f"{f['column']} -> {f['references']}" for f in d["foreign_keys"]),
           "sample_rows": str(d["sample"])[:1500]}
    return {"source": "local_database", "total": 1, "records": [rec]}


def db_query(ctx: ToolContext, sql: str) -> dict[str, Any]:
    db = _db(ctx)
    try:
        res = db.query(sql)
    except DatabaseError as exc:
        raise ToolError("permanent", str(exc)) from exc
    tag = hashlib.sha1(sql.encode()).hexdigest()[:8]
    recs = [{"_row": f"q{tag}:{i + 1}", "_db": db.name, **dict(zip(res["columns"], row))}
            for i, row in enumerate(res["rows"])]
    note = f"{res['row_count']} row(s)" + (" (cut at 200: narrow the query)" if res["truncated"] else "")
    return {"source": "local_database", "total": len(recs), "records": recs, "note": note}
