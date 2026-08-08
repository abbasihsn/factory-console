#!/usr/bin/env bash
#
# Dev hot-reload loop: Uvicorn (--reload) + Vite dev with the /api proxy.
#
# PY_PORT is a REAL knob: it is exported below so frontend/vite.config.ts derives
# its /api proxy target from the same value, instead of the two disagreeing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

PY_PORT="${PY_PORT:-8000}"
FE_PORT="${FE_PORT:-5173}"

# Exported so Vite's config reads the SAME port this script starts uvicorn on.
export PY_PORT

# Put the worktree's package on the path so Uvicorn imports it without an
# editable install (pyproject only wires server/ onto the path for pytest).
export PYTHONPATH="$ROOT/server${PYTHONPATH:+:$PYTHONPATH}"

# create_app now requires a file_adapter, so Uvicorn boots the zero-arg
# create_dev_app factory, which discovers the project root and wires RealFileAdapter.
uvicorn factory_console.app:create_dev_app --factory --reload --port "$PY_PORT" --host 127.0.0.1 &
UVICORN_PID=$!

cleanup() {
	kill "$UVICORN_PID" 2>/dev/null || true
	wait "$UVICORN_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait for the backend to actually answer before starting Vite. `set -e` cannot see
# a BACKGROUNDED process die, so without this a port that is already taken made
# uvicorn exit immediately and the script carried on to `pnpm dev` — proxying /api
# to whatever else held the port, with no error anywhere. Mirrors the bounded
# readiness poll in scripts/smoke.sh.
for _ in $(seq 1 50); do
	if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
		echo "dev: uvicorn exited before it was ready (is port $PY_PORT already in use?)" >&2
		exit 1
	fi
	if curl -fsS -o /dev/null "http://127.0.0.1:$PY_PORT/api/v1/health" 2>/dev/null; then
		break
	fi
	sleep 0.2
done

if ! curl -fsS -o /dev/null "http://127.0.0.1:$PY_PORT/api/v1/health" 2>/dev/null; then
	echo "dev: timed out waiting for the backend on port $PY_PORT" >&2
	exit 1
fi

# Vite runs in the foreground; when it exits (Ctrl-C) the trap tears down Uvicorn.
cd "$ROOT/frontend"
pnpm dev --port "$FE_PORT"
