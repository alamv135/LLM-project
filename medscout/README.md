# MedScout — Group 12, COMP7608B

**An agentic system for evidence-grounded MedTech investment diligence.**
MedScout reads a company's materials, extracts the material claims, plans public-evidence
checks, researches each claim in a bounded tool-using loop (openFDA, ClinicalTrials.gov,
PubMed and the company's own documents), verifies its own verdicts against the record, and
writes a traceable **Investment Readiness Brief** with citations, possible conflicts, open
questions and a source log.

Members: Fung Chin Chun (u3665702) · Tsoi Wai Chit Alvin (u3666009) · Lam Leung Fong (u3665643) · Tsang Yiu Ming (u3604469)

> Status: **minimal viable project (skeleton)**. The full pipeline runs end to end offline with a
> scripted mock model and a synthetic data mirror. Real-model and live-API paths are implemented;
> see [Mocks, unverified parts and limitations](#mocks-unverified-parts-and-limitations).

---

## Quickstart

```bash
python3 -m venv .venv && . .venv/bin/activate      # Python 3.10+ (tested on 3.13)
pip install -r requirements.txt
```

```bash
python run.py demo                      # fictional Nordleaf Cardio case, offline, mock model
python run.py test                      # 35 unit/integration tests, no model or network needed
python run.py eval                      # 4 scenarios x {medscout, direct, rag} -> eval/results/
```

Each run writes `runs/<run_id>/brief.md` (the deliverable), `trace.jsonl` (every model call,
tool call, decision, verification), `record.json` (full research record) and `summary.json`.

### Other commands

```bash
python run.py demo --interactive                      # agent may ask you to confirm an entity match
python run.py demo --focus regulatory                 # changed request: regulatory claims only
python run.py demo --transport mcp                    # public-source tools served over MCP (FastMCP)
python run.py resume <run_id>                         # continue an interrupted run from its checkpoint
python run.py probe --case nordleaf --claim C1 --show-context   # one decision, no tool execution
python run.py tool-check fda_510k_search '{"applicant": "Nordleaf Cardio"}' --case nordleaf
MEDSCOUT_FAULTS="fda_510k_search=timeout*2" python run.py demo  # fault injection
MEDSCOUT_MOCK_FAULTS="malformed:1,ungrounded:1" python run.py demo
```

### Use a real model

| Provider | Setup | Command |
|---|---|---|
| Ollama (local, Lec 4) | `ollama pull llama3.2:3b` (a tool-capable 3B+ model is recommended; 1B struggles with the JSON protocol) | `python run.py demo --provider ollama --model llama3.2:3b` |
| OpenAI-compatible | `export OPENAI_BASE_URL=... OPENAI_API_KEY=...` | `python run.py demo --provider openai --model <model>` |
| Azure-style (e.g. HKU GenAI quota, if Azure-compatible) | `export AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... AZURE_OPENAI_API_VERSION=...` | `python run.py demo --provider openai --model <deployment>` |

Cost accounting: `export MEDSCOUT_PRICE_IN=<USD per 1M input tokens> MEDSCOUT_PRICE_OUT=<...>`.
Keep credentials in environment variables only (never in the repo / submission).

### Use live public data

```bash
python run.py run --company "Real Co" --product "Device" --materials path/to/docs --backend live --provider ollama
```

Optional: `OPENFDA_API_KEY`, `NCBI_EMAIL`, `NCBI_API_KEY`. Responses are cached in SQLite for 24 h
(`MEDSCOUT_CACHE_TTL_S`). Set `MEDSCOUT_RECORD_DIR=dir` to save raw responses for reproducible evaluation.

---

## Architecture

```
            ┌──────────────────────────── runtime / harness (limits · validation · recovery · trace) ─────────────────────────────┐
 company    │  intake ─► extract_claims ─► plan ─► research (per task) ─► reconcile ─► brief                                     │
 materials ─┤                                       │                                                                           │
            │      ┌────────────────────────────────▼─────────────────────────────────┐                                         │
            │      │ context builder: [rules+tool catalog+skill index] [task brief +  │   memory (SQLite)                       │
            │      │  LOADED_SKILL] [frozen history] [WORKING_STATE tail]  + budget   │◄── semantic: confirmed aliases          │
            │      └───────────────┬──────────────────────────────────────────────────┘    episodic: past cases                 │
            │                      ▼                                                         checkpoints · HTTP cache             │
            │        LLM decision: tool_calls | ask_user | finish                                                               │
            │                      ▼                                                                                            │
            │   parse + schema + allowlist ─► tool gateway (parallel, timeout, retry, output check, evidence ids) ─► observation │
            │                      ▼                                     │ local functions │ MCP server (FastMCP) │             │
            │   finish ─► verifier (grounding + domain guardrails) ─► accept | revise once | downgrade to unresolved            │
            └───────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Where each lecture concept lives

| Lecture concept | Implementation |
|---|---|
| Agent–environment loop, bounded agent loop, stopping rules (L4) | `medscout/harness.py` — step / tool / time budgets, `finish`, `ask_user`, handoff |
| Runtime / harness responsibilities (L4) | `harness.py`, `workflow.py` (before inference: context; after output: validate; after execution: record; before continuing: limits) |
| Planning / task decomposition (L4) | `workflow.plan` + `prompts/plan.md`; Python coverage check adds tasks for missed claims |
| ReAct: observations change the next action (L4) | Name-variant retries, product-code follow-ups, revision after `VERIFICATION_FAILED` |
| JSON tool protocol, parsing & dispatch, allowlist (L4, L5) | `medscout/protocol.py`, `tools/registry.py`, `tools/gateway.py` |
| Tool contracts: input + output schemas, structured errors (L4, L5) | `tools/base.py`, `tools/openfda.py`, `tools/clinicaltrials.py`, `tools/pubmed.py` |
| Parallel calls with call ids (L5) | `ToolGateway.run_batch` (asyncio.gather, ids bind results to calls) |
| Fault tolerance: transient / permanent / unavailable, bounded retry + backoff + jitter (L4, L5) | `ToolGateway._execute_with_retry`; fault injection via `MEDSCOUT_FAULTS` |
| Constrained generation + runtime validation (L5) | Ollama `format` (JSON schema) / OpenAI `json_object` + jsonschema validation; one format repair |
| MCP discovery (`tools/list`) and invocation (`tools/call`) (L4, L5) | `medscout/mcp_server.py` (FastMCP) + MCP transport in `gateway.py` |
| Context builder, stable prefix, volatile tail, cache layout (L4, L5) | `medscout/context.py`, `prompts/system.md` |
| Context budget and compression (structured extraction, recoverable originals) (L4, L5) | `Observation.for_model`, `ContextBuilder.build`, `get_evidence` tool |
| Skills: SKILL.md, discover → activate → references (L4) | `skills/*/SKILL.md`, `medscout/skills.py`, `read_skill_reference` tool |
| Memory: working state vs semantic / episodic, controlled writes (L4) | `medscout/state.py` (working), `medscout/memory.py` (aliases written only after human confirmation) |
| Checkpoints and resume (L4) | `Workflow.checkpoint`, `workflow.resume`, `python run.py resume` |
| Human in the loop (L4 "ask permission", proposal C4) | `ask_user` action → `--interactive`; non-interactive runs record the doubt as an open question |
| Tracing: inputs, outputs, time, cost (L4) | `medscout/trace.py` → `runs/<id>/trace.jsonl` |
| Latency / cost accounting, p50/p95 (L4) | `llm.call_model`, `Tracer.summary`, brief run log |
| Testing strategy: parser checks, fault injection, end-to-end, regression (L4) | `tests/`, `eval/` |

### Which decisions are the model's, which are Python's

* **Model**: which claims are material; how to plan and prioritise; which tool, with which
  arguments, in which order; when to try a name variant; when to ask the reviewer; when to stop;
  the verdict and its rationale.
* **Python**: chunking with source locations; schema and allowlist checks; budgets, deadlines,
  retries; evidence ids and provenance; grounding checks (cited ids exist, were retrieved in this task,
  substantive verdicts need independent evidence, "FDA approved" vs 510(k) guardrail); cross-document
  numeric consistency; claim coverage; brief rendering; memory writes.

### Verdicts

`publicly_supported` · `partially_supported` · `possibly_contradicted` · `company_reported_only` · `unresolved`
(proposal §4.1). Company materials are never counted as independent support.

---

## Reliability and recovery (tested)

| Failure situation | Detection | Bounded recovery | Test |
|---|---|---|---|
| Malformed model output | JSON / schema / allowlist check | one format repair, then controlled stop | `test_format_repair_and_verification_revision` |
| Ungrounded verdict (unknown / unseen evidence id, no independent source) | verifier | one revision, then downgrade to `unresolved` | same + `TestVerifier` |
| Tool timeout / 5xx / 429 | exception classification | ≤2 retries with backoff + jitter (reads only) | `test_transient_fault_*` |
| Source unavailable (blocked host, no mirror) | `unavailable` class | no retry; verdict `unresolved` with open question | `test_failure_case_reports_honest_gaps` |
| Invalid tool arguments | input schema | structured `permanent` error returned to the model | `test_input_schema_rejects_bad_arguments` |
| Ambiguous entity (applicant name ≠ company) | model asks | one question per task; alias stored only if confirmed | `test_human_confirmed_alias_becomes_memory` |
| Step / tool / time budget reached | harness counters | handoff: `unresolved`, reason recorded | `test_step_budget_handoff` |
| Process crash / Ctrl-C | checkpoint after every node and task | `python run.py resume <id>` | `test_resume_from_checkpoint` |

## Evaluation

`python run.py eval [--provider ...] [--systems medscout,direct,rag] [--scenarios ...]`

Scenarios (`eval/cases.json`): ordinary task (Nordleaf), changed request (regulatory focus),
failure case (Quillon: no public records + injected timeouts / unavailable registry), and a
robustness case (malformed output, ungrounded citation, transient timeout).
Metrics (proposal Table 1): claim recall, verdict accuracy, false-resolution rate, evidence support,
conflict recall, false-conflict rate, tool-error rate, model/tool calls, tokens, cost, wall time.
Baselines (proposal §5.2): **Direct LLM** (one call, no tools) and **Standard RAG** (one fixed retrieval
round + one synthesis call), same model and settings.

## Project layout

```
run.py                     CLI entry point
medscout/
  workflow.py              outer graph: intake → extract → plan → research → reconcile → brief (+ checkpoints)
  harness.py               bounded ReAct loop for one research task
  context.py               context builder, budget, compression
  protocol.py              JSON output contracts + parser
  verify.py                grounding checks and domain guardrails
  llm.py                   mock / Ollama / OpenAI-compatible clients, accounting
  mock_policy.py           MOCK scripted model for offline runs and tests
  skills.py  memory.py  trace.py  intake.py  brief.py  state.py  config.py
  baselines.py  evaluation.py  mcp_server.py
  tools/                   contracts, gateway, openFDA, ClinicalTrials.gov, PubMed, local tools, mirror backend
prompts/                   system.md (stable header), extract.md, plan.md, baseline*.md
skills/                    claim-extraction, regulatory-check (+ references/), clinical-evidence,
                           competitive-context, commercial-signals
data/cases/                nordleaf (ordinary), quillon (failure) — FICTIONAL, synthetic mirrors
eval/cases.json            scenarios + reference outcomes
tests/                     unittest suite (python run.py test)
```

## Mocks, unverified parts and limitations

* **Mock model** (`--provider mock`, default): a keyword-rule policy that reads only the assembled
  context and emits the real protocol. It exists to test the plumbing. Its rules were written alongside
  the demo case, so **mock evaluation numbers are not evidence of quality** — report real-model runs.
* **Synthetic data**: both demo companies, all their documents and every record in
  `data/cases/*/public_mirror/` are invented (product codes ZXA/ZXB, NCT/PMID/DOI ids, authors).
  The brief flags synthetic evidence.
* **Live APIs not verified from the build environment**: the openFDA, ClinicalTrials.gov and PubMed
  hosts were blocked there, so the live clients follow the public API documentation but have not been
  run against the real services. Validate with `tool-check --backend live` first.
* **Real model clients** are tested against local fake HTTP endpoints only (`tests/test_llm_http.py`).
  The HKU GenAI endpoint format was not confirmed; adjust `llm.py` if it differs from the
  OpenAI/Azure formats.
* Not yet integrated: CMS coverage, NIH RePORTER, patents, web pages, DOCX/PPTX, OCR, PubMed abstracts,
  De Novo database. Matching uses simple lexical rules; a retriever/reranker would help on paraphrases.
* Token counts for the mock are estimates (chars / 4).

## Next steps (suggested)

1. Run the eval with a real model (Ollama 3B/8B and one hosted model), tune `prompts/` and skills, record results.
2. Build 2–3 real-company evidence packets from public materials; record live responses for replay.
3. Add CMS Coverage API and NIH RePORTER tools (contract + normaliser + mirror + skill update).
4. Add an LLM-judge with explicit criteria plus a human check for rationale quality (final-report requirement).
5. Optional: port `workflow.py` to a LangGraph `StateGraph` (nodes map 1:1; `interrupt()` for `ask_user`).

## AI assistance

This skeleton was produced with AI assistance (Claude Code). Per the course guideline, submit the
project-related prompts and a short declaration of which tools/models were used and what they helped with.
