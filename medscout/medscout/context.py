"""Context builder (Lec 4 "Context builder", Lec 5 "An example context layout").

Serialized order — stable parts first so a provider's prefix cache can hit
(Lec 5 "Cache layout"):

  [system]  general rules + output contract + TOOL CATALOG + SKILL INDEX   (identical for every call)
  [user]    task brief: LOADED_SKILL + claims + source excerpts + aliases (fixed within a task)
  [...]     frozen history: assistant decisions and <application_observation> results
  [tail]    WORKING_STATE: step, budgets, deadline, instruction          (volatile, appended last)

Budget: I + H + U + T + E + M + Y <= W. When the estimate exceeds the input
budget, the oldest observations are replaced by compact summaries that keep
call ids and evidence ids; the full records stay in the evidence store and can
be recovered with get_evidence (Lec 5 "Context compression").
"""

from __future__ import annotations

import json
from typing import Any

from .llm import messages_tokens

OBS_OPEN, OBS_CLOSE = "<application_observation>", "</application_observation>"


def observation_message(payload: list[dict[str, Any]]) -> dict[str, Any]:
    full = f"{OBS_OPEN}\n{json.dumps(payload, ensure_ascii=False)}\n{OBS_CLOSE}"
    summary = [{"call_id": o.get("call_id"), "tool": o.get("tool"), "arguments": o.get("arguments"),
                "ok": o.get("ok"), "total": o.get("total", 0),
                "evidence_ids": [r.get("evidence_id") for r in o.get("records", []) if r.get("evidence_id")],
                "error": o.get("error", "")} for o in payload]
    short = (f"{OBS_OPEN} (compressed: record details omitted, call get_evidence(evidence_id) to recover)\n"
             f"{json.dumps(summary, ensure_ascii=False)}\n{OBS_CLOSE}")
    return {"role": "user", "content": full, "summary": short}


class ContextBuilder:
    def __init__(self, system_text: str, limits):
        self.system_text = system_text
        self.limits = limits

    @property
    def input_budget(self) -> int:
        return self.limits.context_window - self.limits.reserved_output

    def one_shot(self, user_content: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": self.system_text}, {"role": "user", "content": user_content}]

    def build(self, task_brief: str, history: list[dict[str, Any]], dynamic_tail: str
              ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        head = self.one_shot(task_brief)
        hist = [dict(m) for m in history]
        tail_tokens = len(dynamic_tail) // 4 + 4

        def size() -> int:
            return messages_tokens(head) + messages_tokens(hist) + tail_tokens

        compressed = 0
        for m in hist:                      # oldest first
            if size() <= self.input_budget:
                break
            if "summary" in m and m["content"] != m["summary"]:
                m["content"] = m["summary"]
                compressed += 1
        if hist and hist[-1]["role"] == "user":
            hist[-1]["content"] = hist[-1]["content"] + "\n\n" + dynamic_tail
        else:
            hist.append({"role": "user", "content": dynamic_tail})
        msgs = head + [{"role": m["role"], "content": m["content"]} for m in hist]
        tokens = messages_tokens(msgs)
        return msgs, {"context_tokens": tokens, "compressed_observations": compressed,
                      "over_budget": tokens > self.input_budget}
