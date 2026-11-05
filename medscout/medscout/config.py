"""Runtime configuration: model settings, execution limits and paths.

Limits are application configuration choices (Lec 4, "Termination and retry
safety"). Every limit is enforced by the harness, not by the model.
Values can be overridden with environment variables or CLI flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Limits:
    # Outer workflow
    max_tasks: int = 12                 # research tasks per run
    run_deadline_s: float = 900.0       # whole-run time budget
    # Inner research loop (one task)
    max_steps_per_task: int = 6         # model decisions per task
    max_tool_calls_per_task: int = 8    # executed tool calls per task
    max_calls_per_batch: int = 3        # parallel calls in one decision
    max_format_repairs: int = 1         # bounded repair of malformed output
    max_verify_revisions: int = 1       # bounded revision after failed verification
    task_deadline_s: float = 120.0
    # Tool gateway
    tool_timeout_s: float = 10.0
    tool_max_retries: int = 2           # transient read failures only
    retry_base_delay_s: float = 0.5
    # Context budget (Lec 4, "Context budget": I+H+U+T+E+M+Y <= W)
    context_window: int = 8192
    reserved_output: int = 1024
    max_records_per_observation: int = 5
    # Cost (router provider): stop spending API money on this run after this many USD (0 = no limit)
    max_cost_usd: float = 0.0


@dataclass
class Settings:
    provider: str = field(default_factory=lambda: _env("MEDSCOUT_PROVIDER", "mock"))
    model: str = field(default_factory=lambda: _env("MEDSCOUT_MODEL", ""))
    temperature: float = float(_env("MEDSCOUT_TEMPERATURE", "0"))
    # Data backend for public sources: "mirror" (local, offline) or "live" (HTTP APIs)
    backend: str = field(default_factory=lambda: _env("MEDSCOUT_BACKEND", "mirror"))
    # Tool transport: "local" (in-process functions) or "mcp" (FastMCP server over stdio)
    tool_transport: str = field(default_factory=lambda: _env("MEDSCOUT_TOOL_TRANSPORT", "local"))
    interactive: bool = False
    # Router provider: may cloud models read uploaded (private) company documents?
    allow_cloud: bool = field(default_factory=lambda: _env("MEDSCOUT_ALLOW_CLOUD", "0") == "1")
    # Let the agent query the user's own SQLite database (path set in the console / MEDSCOUT_DATA_DB)
    use_database: bool = False
    # Price per million tokens, for cost accounting (set for your provider)
    price_in_per_m: float = float(_env("MEDSCOUT_PRICE_IN", "0"))
    price_out_per_m: float = float(_env("MEDSCOUT_PRICE_OUT", "0"))
    # Paths
    prompts_dir: Path = ROOT / "prompts"
    skills_dir: Path = ROOT / "skills"
    data_dir: Path = ROOT / "data"
    runs_dir: Path = field(default_factory=lambda: Path(_env("MEDSCOUT_RUNS_DIR", str(ROOT / "runs"))))
    db_path: Path = field(default_factory=lambda: Path(_env("MEDSCOUT_DB", str(ROOT / "runs" / "medscout.db"))))
    limits: Limits = field(default_factory=Limits)

    def default_model(self) -> str:
        if self.model:
            return self.model
        return {
            "mock": "mock-scripted-policy",
            "router": "auto",
            "ollama": "qwen3.5:9b",       # tools + vision + thinking; ~6.6 GB, fits a 16-24 GB Mac
            "openai": "gpt-4o-mini",
        }.get(self.provider, "")
