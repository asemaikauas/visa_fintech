#!/bin/bash
# Agent Shield concept demo: one-command start (macOS).
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed. Get it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

if [ ! -f .venv/.installed ]; then
  echo "First run: installing (about a minute, needs internet)..."
  python3 -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip || true
  .venv/bin/python -m pip install -q -r requirements.txt
  touch .venv/.installed
fi

[ -f .env ] || cp .env.example .env

PORT="${PORT:-8000}"
export PORT
(sleep 2 && open "http://localhost:$PORT") >/dev/null 2>&1 &
exec .venv/bin/python -m app.server
