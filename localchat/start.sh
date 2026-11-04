#!/bin/bash
# Start Local Chat on a Mac (works on Linux too).
#   First run: checks Python and Ollama, downloads the model if needed, installs two small Python
#   packages (pypdf for PDFs, certifi for HTTPS). Then starts the server and opens your browser.
# Options (environment variables): LOCALCHAT_MODEL=qwen3.5:9b  LOCALCHAT_PORT=8800
set -u
cd "$(dirname "$0")" || exit 1
MODEL="${LOCALCHAT_MODEL:-qwen3.5:9b}"
PORT="${LOCALCHAT_PORT:-8800}"
OLLAMA="http://127.0.0.1:11434"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n\n' "$*"; read -r -p "Press Return to close this window..." _; exit 1; }

# Already running? Just open the page.
if curl -s -m 2 "http://127.0.0.1:$PORT/api/settings" 2>/dev/null | grep -q '"data_dir"'; then
  say "Local Chat is already running: http://127.0.0.1:$PORT"
  (open "http://127.0.0.1:$PORT" || xdg-open "http://127.0.0.1:$PORT") >/dev/null 2>&1
  exit 0
fi

# 1. Python 3.9 or newer
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || fail "Python 3.9 or newer is needed. Install it with:   xcode-select --install
(or download it from https://www.python.org/downloads/), then run this again."

# 2. Ollama installed and running
if ! curl -s -m 2 "$OLLAMA/api/version" >/dev/null 2>&1; then
  if [ -d /Applications/Ollama.app ]; then
    say "Starting Ollama..."
    open -a Ollama
  elif command -v ollama >/dev/null 2>&1; then
    say "Starting Ollama..."
    (ollama serve >/dev/null 2>&1 &)
  else
    (open https://ollama.com/download || xdg-open https://ollama.com/download) >/dev/null 2>&1
    fail "Ollama is not installed. Download it from https://ollama.com/download , move it to
Applications, open it once, then run this again."
  fi
  for _ in $(seq 1 30); do
    curl -s -m 1 "$OLLAMA/api/version" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -s -m 2 "$OLLAMA/api/version" >/dev/null 2>&1 || fail "Ollama did not start. Open the Ollama app yourself and try again."
fi
echo "Ollama $(curl -s -m 2 "$OLLAMA/api/version" | sed 's/.*"version": *"\([^"]*\)".*/\1/') is running."

# 3. The model (one-time download)
if ! curl -s -m 5 "$OLLAMA/api/tags" | grep -Eq "\"name\": ?\"$MODEL\""; then
  if command -v ollama >/dev/null 2>&1; then
    say "Downloading $MODEL (one time only; qwen3.5:9b is about 6.6 GB)..."
    ollama pull "$MODEL" || fail "The download stopped. Check your internet and run this again (it continues where it stopped)."
  else
    echo "$MODEL is not downloaded yet - the page will show a Download button."
  fi
fi

# 4. Python packages in a private folder (.venv), first run only
if [ ! -x .venv/bin/python ]; then
  say "Setting up Python packages (first run only)..."
  "$PY" -m venv .venv || fail "Could not create the Python environment (.venv)."
fi
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
  if .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt; then
    touch .venv/.installed
  else
    echo "Warning: could not install pypdf / certifi. Chat still works, but PDFs can't be read."
  fi
fi

# 5. Start
say "Starting Local Chat - your browser opens in a moment."
echo "Keep this window open while you chat. To stop: press Ctrl+C or close the window."
ARGS=(--port "$PORT" --open)
[ -n "${LOCALCHAT_MODEL:-}" ] && ARGS+=(--model "$MODEL")
exec .venv/bin/python app.py "${ARGS[@]}"
