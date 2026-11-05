"""RoutedLLM: the MedScout agent's model, chosen per call by the router (or one fixed model).

Used when a run's provider is "router". `model` is either "auto" or one model name
(alias from config/models.json, an Ollama tag, or an OpenRouter id).
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

from ..llm import BaseLLM, LLMError, LLMResult, messages_tokens
from .router import BudgetExceeded, Needs, PrivacyBlocked, Router


class RoutedLLM(BaseLLM):
    name = "router"
    routed = True

    def __init__(self, router: Router, model: str = "auto", temperature: float = 0.0, *, private: bool = False,
                 allow_cloud: bool = False, max_cost: float = 0.0, timeout_s: float = 120.0):
        super().__init__(model or "auto", temperature, timeout_s)
        self.router = router
        self.private = private
        self.allow_cloud = allow_cloud
        self.max_cost = max_cost
        self.failures: dict[str, int] = defaultdict(int)
        think = os.environ.get("MEDSCOUT_OLLAMA_THINK", "").strip().lower()
        self.think = {"true": True, "false": False}.get(think)

    @property
    def auto(self) -> bool:
        return self.model == "auto"

    def note_failure(self, purpose: str, task_id: str = "") -> None:
        """A step's output was unusable (broken JSON, rejected verdict): the next call for this step of this
        task goes to a stronger model. Other tasks keep starting cheap."""
        self.failures[f"{purpose}:{task_id}"] += 1

    def preflight(self) -> None:
        """Fail at the start of a run, not halfway, if the chosen model cannot be used."""
        if not self.auto:
            self.router.single(self.model, Needs(private=self.private, allow_cloud=self.allow_cloud))

    def complete(self, messages: list[dict[str, Any]], schema: dict[str, Any] | None = None, *,
                 purpose: str = "", task_id: str = "", spent: float = 0.0) -> LLMResult:
        needs = Needs(tokens=messages_tokens(messages), private=self.private, allow_cloud=self.allow_cloud,
                      spent=spent, limit=self.max_cost)
        if self.auto:
            decision = self.router.for_run(purpose or "default", needs, self.failures.get(f"{purpose}:{task_id}", 0))
            candidates = [decision.model] + [m for m in self._fallbacks(purpose, needs) if m.alias != decision.model.alias]
        else:
            decision = self.router.single(self.model, needs)
            candidates = [decision.model]
        errors = []
        for i, m in enumerate(candidates):
            try:
                res = self.router.provider(m).complete(
                    m.id, messages, schema, temperature=self.temperature, timeout=self.timeout_s,
                    think=self.think if m.thinking else None, max_tokens=self.router.max_out,
                    **({"json_schema": m.json_schema} if m.kind == "openai" else {}))
            except (BudgetExceeded, PrivacyBlocked):
                raise
            except LLMError as exc:
                errors.append(f"{m.alias}: {exc}")
                continue
            if res.cost_usd is None:
                res.cost_usd = m.cost(res.tokens_in, res.tokens_out, res.cached_tokens)
            res.model = m.alias
            res.route = decision.reason if i == 0 else f"fallback after {'; '.join(errors)[:300]}"
            return res
        raise LLMError("; ".join(errors) or "no model available")

    def _fallbacks(self, purpose: str, needs: Needs) -> list:
        """Other models from the step's list that pass the checks, for when the first one errors."""
        table = self.router.reg.router_cfg.get("run") or {}
        names = table.get(purpose) or table.get("default") or []
        out = []
        for n in names:
            try:
                m = self.router.reg.get(n)
            except KeyError:
                continue
            if not self.router.check(m, needs):
                out.append(m)
        return out
