import json
import os
import tempfile
from pathlib import Path

from medscout.config import ROOT, Settings
from medscout.state import RunState

CASES = ROOT / "data" / "cases"


def temp_settings(**over) -> Settings:
    tmp = Path(tempfile.mkdtemp(prefix="medscout-test-"))
    s = Settings()
    s.provider, s.backend, s.tool_transport = "mock", "mirror", "local"
    s.runs_dir, s.db_path = tmp / "runs", tmp / "test.db"
    for k, v in over.items():
        setattr(s, k, v)
    return s


def new_state(case: str = "nordleaf", focus=None) -> tuple[RunState, Path]:
    cdir = CASES / case
    meta = json.loads((cdir / "case.json").read_text())
    return RunState.new(meta["company"], str(cdir / "materials"), meta["product"], focus or []), cdir


class env:
    """Temporarily set environment variables."""

    def __init__(self, **kv):
        self.kv, self.old = kv, {}

    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.kv}
        os.environ.update(self.kv)

    def __exit__(self, *a):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
