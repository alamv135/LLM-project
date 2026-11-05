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
python run.py ui --open                 # web interface + run controller at http://127.0.0.1:8765
python run.py demo                      # same agent from the terminal: fictional Nordleaf Cardio case, offline
python run.py test                      # 84 unit/integration tests, no model or network needed
python run.py eval                      # 4 scenarios x {medscout, direct, rag} -> eval/results/
```

Each run writes `runs/<run_id>/brief.md` (the deliverable), `trace.jsonl` (every model call,
tool call, decision, verification), `record.json` (full research record) and `summary.json`.

## Web interface and run controller

`python run.py ui` starts a local server (standard library only, bound to 127.0.0.1) and serves a
browser console. Use `--port` to change the port and `--open` to open the browser.

| Tab | What you can do |
|---|---|
| **New run** | Pick a demo case or upload your own `.md/.txt/.pdf` documents; set provider, model, temperature, token prices, offline mirror vs live APIs, local vs MCP tools; limit the scope to themes or categories; edit every budget (steps, tool calls, parallel calls, timeouts, retries, context window...); inject tool or model faults; choose how much control you want. |
| **Control** | Watch the run live (phase, tasks, activity feed, tokens, cost) and steer it: **pause / resume / stop**, **step** through each model decision, **approve, edit or reject** each tool batch, **review claims** and **review the plan** (reorder, drop, change skill, priority or question), **answer the agent's questions**, send **notes** to the agent, **skip** a task. Gates can be switched on or off while the run is going. |
| **Results** | Rendered brief, findings with rationale / evidence / conflicts / open questions, source log, claims; download brief.md, record.json, trace.jsonl. |
| **History** | Every saved run; open it or **resume** a stopped or interrupted run from its checkpoint. |
| **Ask** | A free chat with the local model (see [Ask](#ask-free-questions) below): web search, FDA / ClinicalTrials.gov / PubMed lookups with numbered sources, file uploads, and **Ask about this run** (from Results or History) to discuss a finished brief. |
| **Tools** | Run any tool through the real gateway (validation, timeout, retries, fault injection) without a model. |
| **Prompts & skills** | Edit `prompts/*.md`, every `SKILL.md` and reference, and `eval/cases.json`; changes apply to the next run. |
| **Evaluation** | Run the scenarios against MedScout and the baselines and compare the metrics; load past results. |
| **Model connection** | Set OpenAI-compatible / Azure / Ollama endpoints and keys (kept in server memory only, never written to disk) and test the connection. |

How the controller works (`medscout/control.py`): the runtime calls `gate()` before every phase,
task and model decision, and `request()` when it needs a human (claim review, plan review, tool approval,
a question, a step). The run thread blocks until you answer in the browser. Time spent waiting for you
is excluded from the run and task deadlines. A stop checkpoints the run so it can be resumed later.
Only one run (or evaluation) executes at a time.

### Ask: free questions

The **Ask** tab is a private, Claude-style chat with your Ollama model, built into the console
(code in `medscout/ask/`). Use it for anything that is not a full diligence run: "what is a De Novo
request?", "does this company have a 510(k)?", "explain this table in the brief", "read this PDF".

* Answers stream in with Markdown, tables, code and math. Chats are saved on the left.
* **Web search** (globe button): the model searches (DuckDuckGo, or Ollama's web search API with a key
  in Ask's settings) and reads pages, then cites them as [1] [2]. Click a number to open the source.
* **FDA & trials** (flask button): the model can call MedScout's own `fda_510k_search`,
  `fda_pma_search`, `fda_classification_lookup`, `clinicaltrials_search` and `pubmed_search` through
  the same tool gateway as a run (schema check, timeout, retries, cache). Records become numbered
  sources linking to the FDA / registry / PubMed page. In Ask's settings choose **Live** (the real
  services) or a demo case (offline synthetic mirror, labelled as synthetic in the answer).
* **Files**: attach PDF, Word, Excel, PowerPoint, text/code or images (qwen3.5 can read images, e.g.
  a pitch-deck screenshot). Long files are cut to the most relevant parts and the model can search the rest.
* **Ask about this run**: in Results (or History) this button opens Ask with the run's `brief.md`
  attached and a starter question.
* **Think** (light bulb): step-by-step reasoning before the answer; slower, better for hard questions.
* It uses the Ollama address from the **Model connection** tab (`OLLAMA_HOST`), default
  `http://127.0.0.1:11434`, and needs a model with tool support for search. If the model is not
  downloaded, Ask shows a **Download** button.
* Chats, uploads and Ask's settings are stored in `runs/ask/` (git-ignored). Only search queries and the
  pages / records the model looks up leave your computer.

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
| Ollama (local, Lec 4) | `ollama pull qwen3.5:9b` (default; see below. 1B models struggle with the JSON protocol) | `python run.py demo --provider ollama --model qwen3.5:9b` |
| OpenAI-compatible | `export OPENAI_BASE_URL=... OPENAI_API_KEY=...` | `python run.py demo --provider openai --model <model>` |
| Azure-style (e.g. HKU GenAI quota, if Azure-compatible) | `export AZURE_OPENAI_ENDPOINT=... AZURE_OPENAI_API_KEY=... AZURE_OPENAI_API_VERSION=...` | `python run.py demo --provider openai --model <deployment>` |

Cost accounting: `export MEDSCOUT_PRICE_IN=<USD per 1M input tokens> MEDSCOUT_PRICE_OUT=<...>`.
Keep credentials in environment variables only (never in the repo / submission).

### Which local model? (M4 MacBook Air, 24 GB)

Rule of thumb: the model file must fit in memory with room to spare (macOS, the browser and the
context window need ~6-8 GB), and generation speed is roughly *memory bandwidth ÷ GB read per token*.
The M4 MacBook Air reads about 120 GB/s, so a 6.6 GB model tops out near ~18 tokens/s.

| Model | Download | Use it for | Notes |
|---|---|---|---|
| **qwen3.5:9b** (default) | ~6.6 GB | Ask **and** MedScout runs | Tools, vision (images) and optional thinking; 256K context; lots of memory headroom; writes Chinese and English. |
| gpt-oss:20b | ~13-14 GB | Second model in the evaluation | Mixture-of-experts (only ~3.6B parameters active per token), strong at tool use and reasoning. No vision; always reasons; community reports of unreliable strict-JSON output in Ollama, so check MedScout's format-repair counts. |
| llama3.2:3b | ~2 GB | Small-model data point in the evaluation | Fast but weak at multi-step research. |
| 27B+ dense / 35B MoE models | ~17-24 GB | Avoid on 24 GB | Too tight with macOS and the context; ~6 tokens/s on an Air. |

Tips: use the **same model** for Ask and runs (two different models loaded at once can exceed 24 GB).
Ask uses a 32K context and runs use 8K (`OLLAMA_NUM_CTX`); start the console with
`OLLAMA_NUM_CTX=32768 python run.py ui` if switching between them makes Ollama reload the model.
`MEDSCOUT_OLLAMA_THINK=false` (or `true`) switches qwen3.5's thinking off/on for run decisions; unset
uses the model default. The Air has no fan, so long evaluations slow down as it warms up. All of
these numbers are estimates: measure with `python run.py eval --provider ollama --model <model>`.

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
| Checkpoints and resume (L4) | `Workflow.checkpoint`, `workflow.resume`, `python run.py resume`, Resume button in the UI |
| Human in the loop (L4 "ask permission", proposal C4) | `ask_user` action → UI or `--interactive`; non-interactive runs record the doubt as an open question |
| Runtime control points: before inference, after model output, before continuing (L4 harness) | `medscout/control.py` gates in `harness.py` / `workflow.py`: pause, stop, step, tool approval, plan and claim review, notes, skip |
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
| Reviewer stops the run | `StopRequested` at the next gate | checkpoint; Resume continues the unfinished task | `test_stop_then_resume`, `test_2_stop_and_resume_over_http` |
| Reviewer takes a long time to answer | waiting time is tracked | excluded from run and task deadlines | `test_pause_time_is_excluded_from_deadlines` |

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
  control.py               run controller: gates, reviewer requests, notes, skip, pause/stop
  web/server.py            local web server + JSON API (python run.py ui)
  web/static/              browser console (index.html, app.js, app.css; no build step)
  ask/                     the Ask tab: chat loop (chat.py), Ollama client, web search + page reading (web.py),
                           file reading (files.py), MedScout tools in chat (medtech.py), routes, static/ page
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
tests/                     unittest suite (python run.py test), incl. controller, web API and Ask tests
                           (Ask runs against tests/fake_ollama.py, a pretend Ollama)
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
* **Ask** was tested against a fake Ollama and a fake search page only: real models, live DuckDuckGo
  results, Safari and the Mac-only file helpers (`sips` for HEIC photos, `textutil` for .doc/.rtf) have
  not been tried yet.

## Next steps (suggested)

1. Run the eval with a real model (Ollama 3B/8B and one hosted model), tune `prompts/` and skills, record results.
2. Build 2–3 real-company evidence packets from public materials; record live responses for replay.
3. Add CMS Coverage API and NIH RePORTER tools (contract + normaliser + mirror + skill update).
4. Add an LLM-judge with explicit criteria plus a human check for rationale quality (final-report requirement).
5. Optional: port `workflow.py` to a LangGraph `StateGraph` (nodes map 1:1; `interrupt()` for `ask_user`).

## AI assistance

This skeleton was produced with AI assistance (Claude Code). Per the course guideline, submit the
project-related prompts and a short declaration of which tools/models were used and what they helped with.
