#!/bin/sh
# Double-click (macOS) or run (Linux) to start the ContextPilot app.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up ContextPilot. This takes a minute..."
  python3 -m venv .venv || { echo "Python 3.10+ is required: https://www.python.org/downloads/"; exit 1; }
  .venv/bin/python -m pip install --quiet --upgrade pip
  .venv/bin/python -m pip install --quiet -e ".[all]"
fi
exec .venv/bin/python -m contextpilot ui "$@"
