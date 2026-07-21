#!/usr/bin/env bash
#
# Dev hot-reload loop: Uvicorn (--reload) + Vite dev with the /api proxy.
#
# Vite's dev proxy target is hardcoded to http://127.0.0.1:8000 in
# frontend/vite.config.ts, so PY_PORT MUST stay 8000 or the frontend can no
# longer reach the backend during development.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

PY_PORT="${PY_PORT:-8000}"  # must match the hardcoded Vite proxy target
FE_PORT="${FE_PORT:-5173}"

# Put the worktree's package on the path so Uvicorn imports it without an
# editable install (pyproject only wires server/ onto the path for pytest).
export PYTHONPATH="$ROOT/server${PYTHONPATH:+:$PYTHONPATH}"

# T20 will swap create_app -> create_dev_app once create_app gains required args.
uvicorn factory_console.app:create_app --factory --reload --port "$PY_PORT" --host 127.0.0.1 &
UVICORN_PID=$!

cleanup() {
	kill "$UVICORN_PID" 2>/dev/null || true
	wait "$UVICORN_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Vite runs in the foreground; when it exits (Ctrl-C) the trap tears down Uvicorn.
cd "$ROOT/frontend"
pnpm dev --port "$FE_PORT"
