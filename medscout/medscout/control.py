"""Run controller: the human side of the harness.

The runtime calls into the controller at fixed points (Lec 4 "Runtime /
harness": before inference, after model output, before continuing):

  gate("decision")      before every model decision   -> pause / step mode / stop
  gate("task")          before every research task    -> pause / stop
  request("review_claims" | "review_plan" | "approve_tools" | "ask_user" | "step")
                        blocks the run until the reviewer answers in the UI
  take_notes(task_id)   reviewer notes injected into the next model context
  should_skip(task_id)  reviewer skipped a task -> controlled handoff

Waiting time is excluded from the run and task deadlines (`now()`), so a
reviewer who takes five minutes to answer does not exhaust the time budget.
A stop raises StopRequested; the workflow checkpoints and the run can be
resumed later from the same point.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

FLAGS = ("step_mode", "approve_tools", "review_plan", "review_claims", "ask_reviewer")


class StopRequested(Exception):
    """The reviewer stopped the run."""


@dataclass
class Pending:
    request_id: str
    kind: str
    payload: dict[str, Any]
    created: float = field(default_factory=time.time)

    def view(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "kind": self.kind, "payload": self.payload,
                "waiting_s": round(time.time() - self.created, 1)}


_NO_RESPONSE = object()


class RunController:
    def __init__(self, emit: Callable[..., None] | None = None, **flags: bool):
        self.flags = {k: bool(flags.get(k, k == "ask_reviewer")) for k in FLAGS}
        self.emit = emit or (lambda kind, **kw: None)
        self._cv = threading.Condition()
        self.paused = False
        self.stop_requested = False
        self.pending: Pending | None = None
        self._response: Any = _NO_RESPONSE
        self.notes: list[dict[str, Any]] = []
        self.skips: set[str] = set()
        self.paused_total = 0.0
        self.current: dict[str, Any] = {}

    # ------------------------------------------------------------ clock
    def now(self) -> float:
        """Monotonic time minus time spent paused or waiting for the reviewer."""
        return time.monotonic() - self.paused_total

    # ------------------------------------------------------------ runtime side
    def gate(self, where: str, **info: Any) -> None:
        self.current = {"where": where, **info}
        if self.stop_requested:
            raise StopRequested()
        if where == "decision" and self.flags["step_mode"]:
            self.request("step", info)
        self._wait_while(lambda: self.paused)

    def request(self, kind: str, payload: dict[str, Any]) -> Any:
        with self._cv:
            if self.stop_requested:
                raise StopRequested()
            self.pending = Pending(uuid.uuid4().hex[:10], kind, payload)
            self._response = _NO_RESPONSE
            self._cv.notify_all()
        self.emit("control", event="waiting", request=kind, request_id=self.pending.request_id)
        self._wait_while(lambda: self._response is _NO_RESPONSE)
        with self._cv:
            resp, self._response, self.pending = self._response, _NO_RESPONSE, None
        self.emit("control", event="answered", request=kind)
        return resp

    def _wait_while(self, cond: Callable[[], bool]) -> None:
        t0 = time.monotonic()
        with self._cv:
            while cond() and not self.stop_requested:
                self._cv.wait(0.5)
        self.paused_total += time.monotonic() - t0
        if self.stop_requested:
            raise StopRequested()

    def take_notes(self, task_id: str) -> list[str]:
        with self._cv:
            mine = [n for n in self.notes if n.get("task_id") in (None, "", task_id)]
            self.notes = [n for n in self.notes if n not in mine]
        return [n["text"] for n in mine]

    def should_skip(self, task_id: str) -> bool:
        return task_id in self.skips

    # ------------------------------------------------------------ reviewer side
    def respond(self, request_id: str, response: Any) -> bool:
        with self._cv:
            if self.pending is None or self.pending.request_id != request_id:
                return False
            self._response = response
            self._cv.notify_all()
            return True

    def pause(self) -> None:
        with self._cv:
            self.paused = True
        self.emit("control", event="paused")

    def resume(self) -> None:
        with self._cv:
            self.paused = False
            self._cv.notify_all()
        self.emit("control", event="resumed")

    def stop(self) -> None:
        with self._cv:
            self.stop_requested = True
            self._cv.notify_all()
        self.emit("control", event="stop_requested")

    def set_flags(self, **flags: Any) -> None:
        for k, v in flags.items():
            if k in self.flags:
                self.flags[k] = bool(v)
        self.emit("control", event="flags", flags=dict(self.flags))
        if not self.flags["step_mode"] and self.pending and self.pending.kind == "step":
            self.respond(self.pending.request_id, {"continue": True})

    def add_note(self, text: str, task_id: str | None = None) -> None:
        with self._cv:
            self.notes.append({"task_id": task_id or None, "text": text.strip()})
        self.emit("control", event="note", task_id=task_id, text=text.strip()[:200])

    def skip(self, task_id: str) -> None:
        self.skips.add(task_id)
        self.emit("control", event="skip", task_id=task_id)

    def view(self) -> dict[str, Any]:
        return {"flags": dict(self.flags), "paused": self.paused, "stop_requested": self.stop_requested,
                "pending": self.pending.view() if self.pending else None, "current": self.current,
                "queued_notes": len(self.notes), "skipped": sorted(self.skips),
                "waited_s": round(self.paused_total, 1)}
