"""The model router: which model gets this call?

Two modes, chosen by the user:
  single  one model (local or API) for everything; the checks below still apply and stop the task
          with a clear message instead of silently switching.
  auto    local models first; API tokens only when the job needs them:
            Ask  - a local model rates the question easy / medium / hard / expert, and the
                   router.ask table in config/models.json maps that to an ordered model list.
            runs - the router.run table maps each step (extract_claims, plan, research) to a list;
                   after a failure in a step (broken JSON, rejected verdict) that step moves up a tier.

Every candidate is checked in order; the first that passes is used:
  1. privacy   private material (uploaded documents, your database) never goes to a cloud model
               unless the user allowed it for this chat / run
  2. ability   tools, images and context size the call needs
  3. ready     API key present / model downloaded in Ollama
  4. budget    the estimated cost of this call fits in what is left of the task's cost limit
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..llm import LLMError
from .providers import OllamaProvider, OpenAIProvider
from .registry import ModelInfo, Registry

DIFFICULTIES = ("easy", "medium", "hard", "expert")
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"difficulty": {"type": "string", "enum": list(DIFFICULTIES)}, "reason": {"type": "string"}},
    "required": ["difficulty", "reason"],
}
CLASSIFY_PROMPT = """You decide how capable an AI model must be to answer a request well. Reply with JSON only.
easy   - greetings, simple facts or definitions, short rewrites, one simple lookup
medium - explanations, summaries of short texts, ordinary web / database / FDA lookups, simple code
hard   - multi-step analysis, comparing many sources, long documents, careful writing, tricky code or math
expert - high-stakes judgement where mistakes are costly: detailed regulatory, clinical or investment
         assessment, complex proofs or programs
Pick the LOWEST level that will still give a good answer."""


class BudgetExceeded(LLMError):
    """The task's cost limit would be exceeded."""
    final = True        # call_model does not retry these


class PrivacyBlocked(LLMError):
    """Private material would be sent to a cloud model the user did not allow."""
    final = True


@dataclass
class Needs:
    tools: bool = False
    vision: bool = False
    tokens: int = 0                 # prompt size estimate
    private: bool = False           # the prompt contains the user's own documents / database data
    allow_cloud: bool = False       # the user allowed cloud models to see private material
    spent: float = 0.0              # USD spent so far in this task
    limit: float = 0.0              # USD cost limit for the task (0 = none)


@dataclass
class Decision:
    model: ModelInfo
    reason: str
    difficulty: str = ""
    skipped: list[str] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {"model": self.model.alias, "label": self.model.label, "local": self.model.local,
                "tier": self.model.tier, "reason": self.reason, "difficulty": self.difficulty,
                "skipped": self.skipped}


class Router:
    def __init__(self, registry: Registry):
        self.reg = registry

    # ----- providers --------------------------------------------------------------------------------
    def provider(self, m: ModelInfo, ollama_url: str | None = None):
        if m.kind == "ollama":
            return OllamaProvider(ollama_url or self.reg.base_url(m.provider))
        p = self.reg.providers.get(m.provider) or {}
        return OpenAIProvider(self.reg.base_url(m.provider), self.reg.api_key(m.provider), bool(p.get("openrouter")),
                              reasoning_field=p.get("reasoning_field", ""),
                              empty_json_retries=int(p.get("empty_json_retries", 0)))

    @property
    def max_out(self) -> int:
        return int(self.reg.router_cfg.get("max_output_tokens", 2048))

    # ----- the checks -------------------------------------------------------------------------------
    def check(self, m: ModelInfo, needs: Needs) -> str:
        """Empty string if `m` may take this call, else the reason it may not."""
        if needs.private and not needs.allow_cloud and not m.local:
            return "private material stays on local models"
        if needs.tools and not m.tools:
            return "cannot use tools"
        if needs.vision and not m.vision:
            return "cannot read images"
        if needs.tokens and m.context and needs.tokens + 512 > m.context:
            return f"context too small ({m.context:,} tokens)"
        ok, why = self.reg.available(m)
        if not ok:
            return why
        if needs.limit > 0 and not m.local:
            est = m.cost(needs.tokens, self.max_out)
            if needs.spent + est > needs.limit:
                return f"cost limit: ~${est:.4f} more would pass ${needs.limit:.2f} (spent ${needs.spent:.4f})"
        return ""

    def pick(self, names: list[str], needs: Needs, why: str, difficulty: str = "") -> Decision:
        skipped = []
        for name in names:
            try:
                m = self.reg.get(name)
            except KeyError:
                skipped.append(f"{name}: not in models.json")
                continue
            problem = self.check(m, needs)
            if not problem:
                return Decision(m, why + (f" (skipped {'; '.join(skipped)})" if skipped else ""), difficulty, skipped)
            skipped.append(f"{m.alias}: {problem}")
        # nothing in the list fits: any local model that can do it keeps the task going
        for m in self.reg.all():
            if m.local and m.alias not in names and not self.check(m, needs):
                return Decision(m, why + f" - fell back to {m.alias} ({'; '.join(skipped)})", difficulty, skipped)
        raise LLMError("no model can take this call: " + "; ".join(skipped))

    def single(self, name: str, needs: Needs) -> Decision:
        """The user's chosen model, or a clear error saying why it cannot be used."""
        m = self.reg.get(name)
        problem = self.check(m, needs)
        if problem.startswith("private"):
            raise PrivacyBlocked(f"{m.label} is a cloud model and this task contains your private documents or "
                                 "database data. Allow cloud models for it, or choose a local model.")
        if problem.startswith("cost limit"):
            raise BudgetExceeded(f"{m.label}: {problem}. Raise the limit or choose a cheaper or local model.")
        if problem and not problem.startswith(("cannot use tools", "cannot read images")):
            raise LLMError(f"{m.label}: {problem}")
        return Decision(m, "chosen by you", skipped=[problem] if problem else [])

    # ----- runs -------------------------------------------------------------------------------------
    def for_run(self, purpose: str, needs: Needs, failures: int = 0) -> Decision:
        table = self.reg.router_cfg.get("run") or {}
        key = purpose if purpose in table else ("baseline" if purpose.startswith("baseline") and "baseline" in table
                                                else "default")
        names = list(table.get(key) or table.get("default") or [m.alias for m in self.reg.all()])
        level = failures // max(1, int(self.reg.router_cfg.get("escalate_after_failures", 1)))
        why = f"step '{purpose}'"
        if level:
            names = self._escalated(names, level)
            why += f", moved up after {failures} failure(s)"
        return self.pick(names, needs, why)

    def _escalated(self, names: list[str], level: int) -> list[str]:
        tiers = []
        for n in names:
            try:
                tiers.append(self.reg.get(n).tier)
            except KeyError:
                tiers.append(0)
        floor = min(tiers, default=0) + level
        higher = [n for n, t in zip(names, tiers) if t >= floor]
        if not higher:      # already at the top of this list: try every stronger model, cheapest first
            top = max(tiers, default=0)
            higher = [m.alias for m in sorted(self.reg.all(), key=lambda m: (m.tier, m.price_out)) if m.tier >= top]
        return higher + [n for n in names if n not in higher]

    # ----- Ask --------------------------------------------------------------------------------------
    def for_ask(self, question: str, needs: Needs, classify: Callable[[str], tuple[str, str]] | None = None,
                escalate_from: str = "") -> Decision:
        table = self.reg.router_cfg.get("ask") or {}
        mention = re.search(r"(?:^|\s)@([\w.:/-]+)", question or "")
        if mention:
            try:
                d = self.single(mention.group(1), needs)
                d.reason = f"you asked for @{mention.group(1)}"
                return d
            except KeyError:
                pass
        if escalate_from:
            base = self.reg.get(escalate_from)
            names = [m.alias for m in sorted(self.reg.all(), key=lambda m: (m.tier, m.price_out)) if m.tier > base.tier]
            if not names:
                raise LLMError(f"{base.label} is already the strongest model in the list")
            return self.pick(names, needs, f"stronger than {base.label}, as you asked", "")
        difficulty, how = heuristic_difficulty(question)
        if not difficulty and classify is not None:
            difficulty, how = classify(question)
        difficulty = difficulty if difficulty in DIFFICULTIES else "medium"
        names = list(table.get(difficulty) or table.get("medium") or [])
        why = f"{difficulty} question ({how})"
        if needs.private and not needs.allow_cloud:
            why += "; your files / data stay on this computer"
        return self.pick(names, needs, why, difficulty)

    def classifier(self, ollama_url: str | None = None) -> Callable[[str], tuple[str, str]]:
        """A function that rates a question with the local classifier model (free)."""
        def classify(question: str) -> tuple[str, str]:
            name = self.reg.router_cfg.get("classifier", "qwen-local")
            try:
                m = self.reg.get(name)
                if not m.local or not self.reg.available(m)[0]:
                    return "", "no local classifier"
                res = self.provider(m, ollama_url).complete(
                    m.id, [{"role": "system", "content": CLASSIFY_PROMPT},
                           {"role": "user", "content": question[:4000]}],
                    CLASSIFY_SCHEMA, timeout=60, num_ctx=4096, think=False, max_tokens=120)
                data = json.loads(res.text)
                return str(data.get("difficulty", "")), "rated by %s: %s" % (m.alias, str(data.get("reason", ""))[:120])
            except (LLMError, KeyError, ValueError, TypeError) as exc:
                return "", f"classifier unavailable ({str(exc)[:80]})"
        return classify


_EASY = re.compile(r"^\s*(hi|hello|hey|thanks?|thank you|ok(ay)?|good (morning|afternoon|evening)|你好|謝謝|谢谢)\b", re.I)
_HARD = re.compile(r"\b(compare|evaluate|assess|analy[sz]e|critique|due diligence|investment (case|memo|thesis)|"
                   r"proof|prove|derive|step[- ]by[- ]step|in detail|comprehensive|strategy)\b", re.I)


def heuristic_difficulty(question: str) -> tuple[str, str]:
    """Free, instant rating for obvious cases; '' means 'ask the classifier'."""
    q = (question or "").strip()
    words = len(q.split())
    if _EASY.match(q) and words <= 8:
        return "easy", "greeting"
    if words <= 6 and not _HARD.search(q):
        return "easy", "short question"
    return "", ""
