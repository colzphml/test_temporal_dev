#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
../.venv/bin/python worker.py &
WORKER_PID=$!
trap 'kill $WORKER_PID 2>/dev/null || true; wait $WORKER_PID 2>/dev/null || true' EXIT
sleep 2
../.venv/bin/python starter.py
