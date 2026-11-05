#!/bin/bash
# Double-click in Finder to start the MedScout console. First start installs what is needed.
cd "$(dirname "$0")" || exit 1
PY=""
for c in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed: https://www.python.org/downloads/"
  read -r -p "Press Return to close..." _
  exit 1
fi
PYTHONUTF8=1 "$PY" launch.py "$@"
