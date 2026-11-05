#!/usr/bin/env python3
"""Easy start for the MedScout console (Windows, macOS, Linux).

Double-click "Start MedScout.bat" (Windows) or "Start MedScout.command" (Mac), or run:
    python launch.py            (options are passed to "run.py ui", e.g. --port 8770)

What it does:
  1. checks Python (3.10 or newer is needed)
  2. first time only: makes a private Python environment in .venv and installs the packages
     from requirements.txt there (again only when requirements.txt changes)
  3. starts Ollama if it is installed but not running (Ask and local models need it)
  4. starts the console and opens http://127.0.0.1:8765 in your browser
     (if it is already running, it just opens the page)
Standard library only, so it runs before anything is installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
REQ = HERE / "requirements.txt"
MARKER = VENV / ".installed"
PORT = 8765
REQUIRED = ["jsonschema"]                       # MedScout cannot start without these
WINDOWS = os.name == "nt"


def say(msg: str) -> None:
    print(msg, flush=True)


def stop(msg: str) -> None:
    say("\n[!] " + msg + "\n")
    sys.exit(1)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


# ----- 1 + 2: Python and packages ----------------------------------------------------------------
def requirement_lines() -> list[str]:
    out = []
    for line in REQ.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def ensure_environment() -> None:
    if sys.version_info < (3, 10):
        stop("MedScout needs Python 3.10 or newer (this is %s).\n    Download it from https://www.python.org/downloads/"
             % sys.version.split()[0] + ("  and tick 'Add python.exe to PATH' while installing." if WINDOWS else ""))
    if not venv_python().exists():
        say("First start: creating a private Python environment in .venv ...")
        import venv
        try:
            venv.create(VENV, with_pip=True)
        except Exception as exc:  # noqa: BLE001
            stop("Could not create .venv: %s\n    Delete the .venv folder and try again." % exc)
    digest = hashlib.sha1(REQ.read_bytes()).hexdigest()
    if MARKER.exists() and MARKER.read_text(encoding="utf-8").strip() == digest:
        return
    py = str(venv_python())
    say("Installing packages (first start, takes a minute) ...")
    pip = [py, "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    lines = requirement_lines()
    core = [r for r in lines if any(r.lower().startswith(n) for n in REQUIRED)]
    if subprocess.call(pip + core) != 0:
        stop("Could not install %s. Check your internet connection and run this again." % ", ".join(core))
    failed = []
    for r in lines:
        if r in core:
            continue
        if subprocess.call(pip + [r]) != 0:
            failed.append(r)
    if failed:
        say("  Note: optional package(s) not installed: %s. MedScout still works (PDF reading or the MCP "
            "transport may be unavailable)." % ", ".join(failed))
    MARKER.write_text(digest, encoding="utf-8")
    say("Packages ready.")


# ----- 3: Ollama --------------------------------------------------------------------------------
def ollama_url() -> str:
    raw = (os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434").strip()
    if "://" not in raw:
        raw = "http://" + raw
    return raw.replace("0.0.0.0", "127.0.0.1").rstrip("/")


def ollama_running() -> bool:
    try:
        with urllib.request.urlopen(ollama_url() + "/api/version", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def start_ollama() -> None:
    if ollama_running():
        say("Ollama is running.")
        return
    started = False
    try:
        if WINDOWS:
            app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
            if app.exists():
                os.startfile(str(app))  # type: ignore[attr-defined]
                started = True
        elif sys.platform == "darwin" and Path("/Applications/Ollama.app").exists():
            subprocess.Popen(["open", "-a", "Ollama"])
            started = True
        if not started and shutil.which("ollama"):
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **({"creationflags": 0x08000000} if WINDOWS else {}))   # no extra window on Windows
            started = True
    except OSError:
        started = False
    if not started:
        say("Ollama is not installed or not running. Local models and the Ask tab need it:\n"
            "    download it from https://ollama.com/download , then run: ollama pull qwen3.5:9b\n"
            "    (The console still opens; API models and the offline mock work without it.)")
        return
    say("Starting Ollama ...")
    for _ in range(20):
        if ollama_running():
            say("Ollama is running.")
            return
        time.sleep(1)
    say("Ollama did not answer yet; it may still be starting. Continuing.")


# ----- 4: the console ------------------------------------------------------------------------------
def port_from_args(args: list[str]) -> int:
    if "--port" in args:
        try:
            return int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    return PORT


def running_copy(port: int) -> str | None:
    """None = nothing (or not MedScout) on this port; else the folder of the MedScout copy serving it
    ("" = an older version that does not say)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/config" % port, timeout=2) as r:
            cfg = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return str(cfg.get("app_root", "")) if "runs_dir" in cfg else None


def same_folder(a: str, b: Path) -> bool:
    try:
        return bool(a) and Path(a).resolve() == b.resolve()
    except OSError:
        return False


def port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main() -> None:
    os.chdir(HERE)
    args = [a for a in sys.argv[1:] if a != "--in-venv"]
    if not in_venv():
        ensure_environment()
        env = dict(os.environ, PYTHONUTF8="1")
        sys.exit(subprocess.call([str(venv_python()), str(HERE / "launch.py"), "--in-venv"] + args, env=env))

    port = port_from_args(args)
    url = "http://127.0.0.1:%d/" % port
    other = running_copy(port)
    if other is not None and same_folder(other, HERE):
        say("MedScout is already running: " + url)
        webbrowser.open(url)
        return
    if other is not None or not port_free(port):
        say("\n[!] Port %d is used by %s." % (port, ("another copy of MedScout (%s)" % other) if other else
            "an OLDER MedScout or another program"))
        say("    Close that window (or the VS Code terminal running it) to stop it. On Windows you can also\n"
            "    end 'python.exe' in Task Manager. This version starts on another port instead.")
        new = next((p for p in range(port + 1, port + 30) if port_free(p) and running_copy(p) is None), None)
        if new is None:
            stop("No free port found near %d." % port)
        if "--port" in args:
            args[args.index("--port") + 1] = str(new)
        else:
            args += ["--port", str(new)]
        port, url = new, "http://127.0.0.1:%d/" % new
    start_ollama()
    say("\nStarting MedScout at %s  (your browser opens in a moment)" % url)
    say("Keep this window open while you work. To stop: press Ctrl+C or close the window.\n")
    if "--open" not in args:
        args.append("--open")
    try:
        sys.exit(subprocess.call([sys.executable, "-X", "utf8", str(HERE / "run.py"), "ui"] + args))
    except KeyboardInterrupt:
        say("\nStopped.")


if __name__ == "__main__":
    main()
