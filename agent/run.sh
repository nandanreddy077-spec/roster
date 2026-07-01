#!/bin/bash
# One command to run Roster locally.
set -e
cd "$(dirname "$0")"

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# Load .env if present (ANTHROPIC_API_KEY etc.)
[ -f .env ] && export $(grep -v '^#' .env | xargs)

# Seed a demo client + sample conversation so the dashboard isn't empty.
.venv/bin/python seed.py

echo ""
echo "Roster running at  http://127.0.0.1:8000/clients"
echo ""
.venv/bin/uvicorn app:app --reload --port 8000
