#!/usr/bin/env bash
# Runs the weekly lead job. This is what cron calls. Works from any folder.
set -euo pipefail
cd "$(dirname "$0")/.."                 # the project folder
export PYTHONUTF8=1                     # business names can be Arabic / Urdu
if [ -x venv/bin/python ]; then PY=venv/bin/python; else PY=python3; fi
exec "$PY" run_weekly.py "$@"
