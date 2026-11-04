"""Tool gateway: validate -> authorise -> execute (bounded) -> validate output -> observe.

Implements Lec 4 "Parsing and dispatch" / "Tool errors and preconditions" and
Lec 5 "Fault tolerance in the runtime":

  before execution : name allowlist + input schema + per-task budget
  execute          : timeout, parallel batch with call ids (asyncio.gather)
  failure          : classify transient / permanent / unavailable;
                     bounded retry with backoff + jitter for transient reads only
  success          : output-schema check, then register evidence with provenance
Every path ends in an Observation the model can read, never an exception.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from ..state import Evidence, RunState, now_iso
from .base import ToolContext, ToolError, ToolSpec
from .registry import PUBLIC_SOURCE_TOOLS

TEXT_FIELDS_TRUNCATE = 300


@dataclass
class Observation:
    call_id: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    data: dict[str, Any] | None = None
    error: str = ""
    error_kind: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    attempts: int = 0
    latency_ms: float = 0.0

    def for_model(self, max_records: int) -> dict[str, Any]:
        """Structured extraction (Lec 5 'Common methods'): keep required fields and exact facts,
        attach evidence ids, drop bulk. Full records stay in the evidence store."""
        out: dict[str, Any] = {"call_id": self.call_id, "tool": self.tool, "arguments": self.arguments, "ok": self.ok}
        if not self.ok:
            out.update(error_kind=self.error_kind, error=self.error)
            return out
        data = self.data or {}
        recs = []
        for i, r in enumerate(data.get("records", [])[:max_records]):
            r2 = {k: (v[:TEXT_FIELDS_TRUNCATE] + "…" if isinstance(v, str) and len(v) > TEXT_FIELDS_TRUNCATE else v)
                  for k, v in r.items() if v not in ("", None, [], {})}
            if i < len(self.evidence_ids):
                r2 = {"evidence_id": self.evidence_ids[i], **r2}
            recs.append(r2)
        out.update(source=data.get("source", ""), total=data.get("total", 0), records=recs)
        if data.get("note"):
            out["note"] = data["note"]
        if data.get("synthetic"):
            out["synthetic_data"] = True
        return out


class ToolGateway:
    def __init__(self, specs: dict[str, ToolSpec], ctx: ToolContext, state: RunState, limits, tracer,
                 transport: str = "local", mcp_server_args: list[str] | None = None, faults: str | None = None):
        self.specs = specs
        self.ctx = ctx
        self.state = state
        self.limits = limits
        self.tracer = tracer
        self.transport = transport
        self.mcp_server_args = mcp_server_args or []
        self.faults = parse_faults(os.environ.get("MEDSCOUT_FAULTS", "") if faults is None else faults)
        self.mcp_tools: set[str] = set()
        self._client = None
        # One background event loop for all tool I/O, so an MCP session can stay open across calls.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True, name="medscout-tools")
        self._thread.start()
        if transport == "mcp":
            self.mcp_tools = self._discover_mcp()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        if self._client is not None:
            try:
                self._submit(self._client.__aexit__(None, None, None))
            except Exception:  # pragma: no cover
                pass
            self._client = None
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._loop.is_running():
            self._loop.close()

    # ------------------------------------------------------------- validation
    def validate(self, call: dict[str, Any]) -> None:
        spec = self.specs.get(call["tool"])
        if spec is None:
            raise ToolError("permanent", f"unknown tool {call['tool']}")
        try:
            jsonschema.validate(call["arguments"], spec.input_schema)
        except jsonschema.ValidationError as exc:
            raise ToolError("permanent", f"invalid arguments for {spec.name}: {exc.message[:200]}") from exc

    # ------------------------------------------------------------- execution
    def run_batch(self, calls: list[dict[str, Any]], *, task_id: str = "", step: int = 0) -> list[Observation]:
        """Execute independent read calls in parallel; each result keeps its call id."""
        bound = [{**c, "call_id": "c_" + uuid.uuid4().hex[:8]} for c in calls]
        return self._submit(self._gather(bound, task_id, step))

    async def _gather(self, calls, task_id, step):
        return await asyncio.gather(*(self._run_one(c, task_id, step) for c in calls))

    async def _run_one(self, call: dict[str, Any], task_id: str, step: int) -> Observation:
        obs = Observation(call_id=call["call_id"], tool=call["tool"], arguments=call["arguments"], ok=False)
        t0 = time.perf_counter()
        try:
            self.validate(call)
            spec = self.specs[call["tool"]]
            data = await self._execute_with_retry(spec, call["arguments"], obs)
            try:
                jsonschema.validate(data, spec.output_schema)
            except jsonschema.ValidationError as exc:
                # A result must satisfy its output contract before it is used as evidence (Lec 5).
                raise ToolError("permanent", f"tool output failed its contract: {exc.message[:160]}") from exc
            obs.ok, obs.data = True, data
            if spec.registers_evidence:
                obs.evidence_ids = self._register(spec, call["arguments"], data)
        except ToolError as exc:
            obs.error, obs.error_kind = exc.message, exc.kind
            self.state.counters.tool_errors += 1
        obs.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.state.counters.tool_calls += 1
        self.tracer.event("tool_call", task_id=task_id, step=step, tool=obs.tool, call_id=obs.call_id,
                          arguments=obs.arguments, ok=obs.ok, error_kind=obs.error_kind, error=obs.error,
                          attempts=obs.attempts, evidence_ids=obs.evidence_ids, latency_ms=obs.latency_ms,
                          transport="mcp" if obs.tool in self.mcp_tools else "local")
        return obs

    async def _execute_with_retry(self, spec: ToolSpec, args: dict[str, Any], obs: Observation) -> dict[str, Any]:
        attempts = 1 + self.limits.tool_max_retries
        last: ToolError | None = None
        for i in range(attempts):
            obs.attempts = i + 1
            try:
                self._inject_fault(spec.name)
                if spec.name in self.mcp_tools:
                    return await self._call_mcp(spec.name, args)
                return await asyncio.wait_for(asyncio.to_thread(spec.handler, self.ctx, **args),
                                              timeout=self.limits.tool_timeout_s)
            except asyncio.TimeoutError:
                last = ToolError("transient", f"timeout after {self.limits.tool_timeout_s}s")
            except ToolError as exc:
                last = exc
            except TypeError as exc:   # unexpected argument reached the handler
                last = ToolError("permanent", f"bad arguments: {exc}")
            if last.kind != "transient" or not spec.read_only:
                break                  # never blindly retry permanent errors or writes
            delay = self.limits.retry_base_delay_s * (2 ** i) * (0.5 + random.random())
            await asyncio.sleep(delay)
        assert last is not None
        if last.kind == "transient" and attempts > 1:
            last = ToolError("transient", f"{last.message} (gave up after {attempts} attempts)")
        raise last

    # ------------------------------------------------------------- evidence
    def register_company_chunks(self, chunks: list[Any]) -> dict[str, str]:
        """Register a claim's own source passages as (company-reported) evidence; return chunk_id -> E-id."""
        spec = self.specs["search_company_docs"]
        recs = [{"chunk_id": c.chunk_id, "doc": c.doc, "location": c.location, "doc_date": c.doc_date,
                 "text": c.text[:500]} for c in chunks]
        ids = self._register(spec, {"source_of_claim": True}, {"records": recs})
        return dict(zip([c.chunk_id for c in chunks], ids))

    def _register(self, spec: ToolSpec, args: dict[str, Any], data: dict[str, Any]) -> list[str]:
        ids = []
        for rec in data.get("records", []):
            ident = str(rec.get(spec.record_id_field, "")) or "?"
            existing = next((e for e in self.state.evidence.values()
                             if e.tool == spec.name and e.identifier == ident), None)
            if existing:
                ids.append(existing.evidence_id)
                continue
            if spec.url_template:
                try:
                    url = spec.url_template.format(**rec)
                except (KeyError, IndexError):
                    url = ""
            else:
                url = f"file://{rec.get('doc', '')}#{rec.get('location', '')}"
            eid = self.state.next_evidence_id()
            self.state.evidence[eid] = Evidence(
                evidence_id=eid, tool=spec.name, source=spec.source_label, identifier=ident, url=url,
                retrieved_at=now_iso(), fields=rec, independent=spec.independent,
                synthetic=bool(data.get("synthetic", False)), query=args)
            ids.append(eid)
        return ids

    # ------------------------------------------------------------- fault injection
    def _inject_fault(self, tool: str) -> None:
        f = self.faults.get(tool) or self.faults.get("*")
        if f and f["remaining"] > 0:
            f["remaining"] -= 1
            raise ToolError(f["kind"], f"injected {f['kind']} fault")

    # ------------------------------------------------------------- MCP transport
    def _discover_mcp(self) -> set[str]:
        """Open one MCP session; tools/list -> intersect with the curated read-only allowlist
        (Lec 5 'Discovery and the proposed batch'). Argument validation still uses the curated
        local contracts, which are stricter than the server-generated schemas."""
        async def _open():
            from fastmcp import Client
            from fastmcp.client.transports import StdioTransport
            server = Path(__file__).resolve().parent.parent / "mcp_server.py"
            client = Client(StdioTransport(command=sys.executable, args=[str(server), *self.mcp_server_args]))
            await client.__aenter__()
            self._client = client
            return [t.name for t in await client.list_tools()]
        try:
            names = set(self._submit(_open()))
        except Exception as exc:  # pragma: no cover - depends on local install
            print(f"[medscout] MCP discovery failed ({exc}); falling back to local tools", file=sys.stderr)
            return set()
        allowed = names & PUBLIC_SOURCE_TOOLS
        self.tracer.event("mcp_discovery", discovered=sorted(names), allowed=sorted(allowed))
        return allowed

    async def _call_mcp(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            res = await self._client.call_tool(name, args, timeout=self.limits.tool_timeout_s, raise_on_error=False)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise ToolError("transient", "MCP call timed out") from exc
        except Exception as exc:
            raise ToolError("transient", f"MCP transport error: {exc}") from exc
        if res.is_error:
            text = " ".join(getattr(c, "text", "") for c in res.content)
            kind = next((k for k in ("transient", "unavailable", "permanent") if text.startswith(k)), "permanent")
            raise ToolError(kind, text[:300])
        return res.structured_content or {}


def parse_faults(spec: str) -> dict[str, dict[str, Any]]:
    """MEDSCOUT_FAULTS="fda_510k_search=timeout*2,pubmed_search=unavailable*1" """
    out: dict[str, dict[str, Any]] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        tool, _, rest = part.partition("=")
        kind, _, count = rest.partition("*")
        kind = {"timeout": "transient", "error": "permanent"}.get(kind, kind) or "transient"
        out[tool] = {"kind": kind, "remaining": int(count or 1)}
    return out
