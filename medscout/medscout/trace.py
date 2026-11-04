"""Execution trace (Lec 4 architecture: "Trace each step: inputs, outputs, time, cost").

Every model call, tool call, decision, verification and checkpoint is one JSON
line in runs/<run_id>/trace.jsonl, so report claims can be linked to code paths.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, round(p * (len(xs) - 1))))
    return round(xs[k], 1)


class Tracer:
    def __init__(self, path: Path | None, run_id: str, echo: bool = False):
        self.path = path
        self.run_id = run_id
        self.echo = echo
        self.events: list[dict[str, Any]] = []
        self.t0 = time.perf_counter()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, kind: str, **fields: Any) -> None:
        ev = {"ts": time.strftime("%H:%M:%S"), "t_ms": round((time.perf_counter() - self.t0) * 1000, 1),
              "run_id": self.run_id, "kind": kind, **fields}
        self.events.append(ev)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        if self.echo:
            self._print(ev)

    def _print(self, ev: dict[str, Any]) -> None:
        k = ev["kind"]
        if k == "tool_call":
            status = "ok" if ev["ok"] else f"{ev['error_kind']}: {ev['error']}"
            print(f"    tool  {ev['tool']}({json.dumps(ev['arguments'])}) -> {status} "
                  f"{ev.get('evidence_ids') or ''} [{ev['latency_ms']} ms, attempts={ev['attempts']}]")
        elif k == "decision":
            print(f"  [{ev.get('task_id')} step {ev.get('step')}] {ev['action']}: {ev.get('summary', '')}")
        elif k in ("phase", "verify", "ask_user", "stop", "format_repair", "plan", "warning"):
            msg = {kk: vv for kk, vv in ev.items() if kk not in ("ts", "t_ms", "run_id", "kind")}
            print(f"  · {k}: {json.dumps(msg, ensure_ascii=False, default=str)[:240]}")

    def summary(self) -> dict[str, Any]:
        model = [e for e in self.events if e["kind"] == "model_call"]
        tools = [e for e in self.events if e["kind"] == "tool_call"]
        ml = [e["latency_ms"] for e in model]
        tl = [e["latency_ms"] for e in tools]
        return {
            "model_calls": len(model),
            "tool_calls": len(tools),
            "tool_errors": sum(1 for e in tools if not e["ok"]),
            "tokens_in": sum(e.get("tokens_in", 0) for e in model),
            "tokens_out": sum(e.get("tokens_out", 0) for e in model),
            "model_latency_p50_ms": _pct(ml, 0.5), "model_latency_p95_ms": _pct(ml, 0.95),
            "tool_latency_p50_ms": _pct(tl, 0.5), "tool_latency_p95_ms": _pct(tl, 0.95),
            "mean_context_tokens": round(statistics.mean([e.get("context_tokens", 0) for e in model]), 1) if model else 0,
            "wall_time_s": round((time.perf_counter() - self.t0), 2),
        }
