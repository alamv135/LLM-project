#!/usr/bin/env python3
"""MedScout command-line entry point.

  python run.py demo                                   # fictional case, offline, mock model
  python run.py run --case nordleaf --provider ollama --model qwen3.5:9b
  python run.py run --case nordleaf --provider router --model auto --max-cost 0.50   # local first, API when needed
  python run.py run --company "Acme Med" --materials path/to/docs --product "AcmeScope" --backend live
  python run.py run --case nordleaf --focus regulatory  # changed request: regulatory claims only
  python run.py resume <run_id>
  python run.py tool-check fda_510k_search '{"applicant": "Nordleaf Cardio"}' --case nordleaf
  python run.py probe --case nordleaf --claim C1        # one model decision, no tool execution
  python run.py eval                                   # scenarios x systems -> eval/results/
  python run.py test                                   # unit + integration tests (no model needed)
  python run.py ui                                     # web console (runs, Ask chat, ...) at http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

if sys.version_info < (3, 10):
    sys.exit("MedScout needs Python 3.10 or newer (you have %s). Get it from https://www.python.org/downloads/"
             % sys.version.split()[0])

if not sys.flags.utf8_mode and os.environ.get("PYTHONUTF8") != "1":
    # Windows (e.g. a Chinese locale) would read MedScout's UTF-8 text files with the wrong encoding.
    # Run again in Python's UTF-8 mode; same arguments, same exit code.
    sys.exit(subprocess.call([sys.executable, "-X", "utf8", __file__] + sys.argv[1:]))

try:
    import jsonschema  # noqa: F401  (needed by MedScout; checked here for a clear message)
except ImportError:
    sys.exit("MedScout's packages are not installed for this Python (%s).\n"
             "Easiest: double-click 'Start MedScout.bat' (Windows) or 'Start MedScout.command' (Mac),\n"
             "or run:  python launch.py\n"
             "Or install them yourself:  python -m pip install -r requirements.txt" % sys.executable)

from medscout.config import ROOT, Settings
from medscout.state import RunState


def _settings(a) -> Settings:
    s = Settings()
    for attr, val in (("provider", a.provider), ("model", a.model), ("backend", a.backend),
                      ("tool_transport", a.transport)):
        if val:
            setattr(s, attr, val)
    s.interactive = bool(getattr(a, "interactive", False))
    if getattr(a, "max_cost", None) is not None:
        s.limits.max_cost_usd = max(0.0, a.max_cost)
    s.allow_cloud = s.allow_cloud or bool(getattr(a, "allow_cloud", False))
    s.use_database = bool(getattr(a, "use_database", False))
    if getattr(a, "max_steps", None):
        s.limits.max_steps_per_task = a.max_steps
    return s


def _ask(question: str, options: list[str]) -> str | None:
    opts = f" [{'/'.join(options)}]" if options else ""
    try:
        ans = input(f"\n  ? REVIEWER INPUT NEEDED: {question}{opts}\n  > ").strip()
    except EOFError:
        return None
    return ans or None


def _case(a) -> tuple[str, str, Path, Path | None]:
    if a.case:
        cdir = ROOT / "data" / "cases" / a.case
        meta = json.loads((cdir / "case.json").read_text())
        return (a.company or meta["company"], a.product or meta.get("product", ""), cdir / "materials", cdir)
    if not (a.company and a.materials):
        sys.exit("provide --case NAME or both --company and --materials")
    return a.company, a.product or "", Path(a.materials), None


def cmd_run(a, demo: bool = False) -> None:
    from medscout.workflow import Workflow
    if demo:
        a.case = a.case or "nordleaf"
    s = _settings(a)
    company, product, materials, case_dir = _case(a)
    focus = [f.strip() for f in (a.focus or "").split(",") if f.strip()]
    st = RunState.new(company, str(materials), product, focus)
    print(f"[medscout] run {st.run_id}: {company} ({product or 'no product'}) · provider={s.provider} "
          f"model={s.default_model()} backend={s.backend} tools={s.tool_transport}")
    if s.backend == "mirror" and case_dir is None:
        print("[medscout] note: no case mirror for ad-hoc materials; public-source tools will report "
              "'unavailable'. Use --backend live.")
    wf = Workflow(s, st, case_dir=case_dir, ask_fn=_ask, echo=not a.quiet)
    wf.run()
    _report(wf)


def _report(wf) -> None:
    st = wf.state
    print(f"\n[medscout] done. Brief: {wf.run_dir / 'brief.md'}")
    print(f"           Trace: {wf.run_dir / 'trace.jsonl'} · Record: {wf.run_dir / 'record.json'}")
    for c in st.claims:
        f = st.findings.get(c.claim_id)
        if f:
            label = "(out of scope)" if f.task_id == "out_of_scope" else f.verdict
            print(f"  {c.claim_id:>4} {c.category:<12} {label:<22} {c.text[:70]}")


def cmd_resume(a) -> None:
    from medscout.memory import Memory
    from medscout.workflow import resume
    s = _settings(a)
    rid = a.run_id or Memory(s.db_path).latest_run_id()
    st = resume(s, rid, ask_fn=_ask, echo=not a.quiet)
    print(f"[medscout] run {st.run_id} phase={st.phase}; brief: {s.runs_dir / st.run_id / 'brief.md'}")


def cmd_tool_check(a) -> None:
    """Run one tool through the gateway (validation, retries, evidence) without a model."""
    from medscout.memory import Memory
    from medscout.skills import SkillLibrary
    from medscout.tools import ToolContext, ToolGateway, build_specs
    from medscout.trace import Tracer
    s = _settings(a)
    mirror = ROOT / "data" / "cases" / a.case / "public_mirror" if a.case else None
    st = RunState.new("tool-check", "")
    ctx = ToolContext(s.backend, mirror, skills=SkillLibrary(s.skills_dir), evidence=st.evidence,
                      cache=Memory(s.db_path), timeout_s=s.limits.tool_timeout_s)
    gw = ToolGateway(build_specs(), ctx, st, s.limits, Tracer(None, st.run_id, echo=True), transport=s.tool_transport,
                     mcp_server_args=["--backend", s.backend] + (["--mirror", str(mirror)] if mirror else []))
    obs = gw.run_batch([{"tool": a.tool, "arguments": json.loads(a.arguments)}])[0]
    gw.close()
    print(json.dumps(obs.for_model(10), indent=2, ensure_ascii=False))


def cmd_probe(a) -> None:
    """Lec 5 'probe': show one research decision for one claim, without executing tools."""
    from medscout.harness import ResearchHarness
    from medscout.llm import call_model
    from medscout.workflow import Workflow
    s = _settings(a)
    company, product, materials, case_dir = _case(a)
    st = RunState.new(company, str(materials), product)
    wf = Workflow(s, st, case_dir=case_dir, echo=False)
    wf.intake()
    wf.extract_claims()
    wf.plan()
    task = next((t for t in st.tasks if a.claim in t.claim_ids), None)
    if task is None:
        sys.exit(f"no task for claim {a.claim}; claims: {[c.claim_id + ': ' + c.text[:50] for c in st.claims]}")
    h = ResearchHarness(llm=wf.llm, gateway=wf.gateway, ctx=wf.ctx, skills=wf.skills, state=st, memory=wf.memory,
                        tracer=wf.tracer, settings=s, ask_fn=None, run_deadline=wf.deadline)
    chunks = [st.chunk(i) for i in st.claim(a.claim).source_chunk_ids]
    brief = h._task_brief(task, wf.gateway.register_company_chunks(chunks))
    msgs, info = wf.ctx.build(brief, [], h._tail(0, 0, wf.deadline, False, False))
    res = call_model(wf.llm, msgs, state=st, tracer=wf.tracer, settings=s, purpose="probe")
    wf.gateway.close()
    if a.show_context:
        for m in msgs:
            print(f"----- {m['role']} -----\n{m['content']}\n")
    print(f"claim {a.claim}: {st.claim(a.claim).text}\ncontext tokens ~{info['context_tokens']}\n"
          f"----- model output -----\n{res.text}")


def cmd_eval(a) -> None:
    from medscout.evaluation import run_eval
    s = _settings(a)
    run_eval(s, systems=[x.strip() for x in a.systems.split(",")],
             scenarios=[x.strip() for x in a.scenarios.split(",")] if a.scenarios else None)


def cmd_ui(a) -> None:
    from medscout.web.server import serve
    serve(host=a.host, port=a.port, settings=_settings(a), open_browser=a.open)


def cmd_test(_a) -> None:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--provider", choices=["mock", "ollama", "openai", "router"],
                        help="router = the model list in config/models.json (--model auto or a name)")
    common.add_argument("--model", help="with --provider router: auto, or claude / deepseek / gemini / qwen-local ...")
    common.add_argument("--max-cost", type=float, help="router: stop spending on API models after this many USD per run")
    common.add_argument("--allow-cloud", action="store_true", help="router: cloud models may read your own documents")
    common.add_argument("--use-database", action="store_true", help="let the agent query your SQLite database (read-only)")
    common.add_argument("--backend", choices=["mirror", "live"], help="public-source data: offline mirror or live APIs")
    common.add_argument("--transport", choices=["local", "mcp"], help="tool transport")
    common.add_argument("--quiet", action="store_true")
    case = argparse.ArgumentParser(add_help=False)
    case.add_argument("--case", help="folder under data/cases (materials/ + public_mirror/)")
    case.add_argument("--company")
    case.add_argument("--product")
    case.add_argument("--materials", help="folder of company materials (.md/.txt/.pdf)")

    ap = argparse.ArgumentParser(description="MedScout — evidence-grounded MedTech diligence agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "demo"):
        p = sub.add_parser(name, parents=[common, case])
        p.add_argument("--focus", help="themes/categories, e.g. regulatory,clinical or product_evidence")
        p.add_argument("--interactive", action="store_true", help="ask the reviewer when the agent requests input")
        p.add_argument("--max-steps", type=int)
    p = sub.add_parser("resume", parents=[common])
    p.add_argument("run_id", nargs="?")
    p.add_argument("--interactive", action="store_true")
    p = sub.add_parser("tool-check", parents=[common])
    p.add_argument("tool")
    p.add_argument("arguments", help="JSON object")
    p.add_argument("--case")
    p = sub.add_parser("probe", parents=[common, case])
    p.add_argument("--claim", default="C1")
    p.add_argument("--show-context", action="store_true")
    p = sub.add_parser("eval", parents=[common])
    p.add_argument("--systems", default="medscout,direct,rag")
    p.add_argument("--scenarios", help="comma list; default: all in eval/cases.json")
    p = sub.add_parser("ui", parents=[common], help="local web interface with the run controller")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open the browser")
    sub.add_parser("test")
    a = ap.parse_args()
    {"run": cmd_run, "demo": lambda x: cmd_run(x, demo=True), "resume": cmd_resume, "tool-check": cmd_tool_check,
     "probe": cmd_probe, "eval": cmd_eval, "ui": cmd_ui, "test": cmd_test}[a.cmd](a)


if __name__ == "__main__":
    main()
