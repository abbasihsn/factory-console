#!/usr/bin/env bash
#
# Smoke-test the built wheel: install it into a throwaway venv, boot the console
# on a random free port (--port 0), and curl the health probe. Proves the wheel
# installs, exposes the factory-console entrypoint, and serves the API.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python}"

# Newest wheel wins so a rebuild is picked up without a stale-artifact surprise.
# ls -t over a controlled dist/ of wheel filenames is safe here (SC2012 N/A).
# shellcheck disable=SC2012
WHEEL="$(ls -t "$ROOT"/dist/*.whl 2>/dev/null | head -n1 || true)"
if [[ -z "$WHEEL" ]]; then
	echo "smoke: no wheel in $ROOT/dist -- run 'make build' or 'make package' first" >&2
	exit 1
fi

VENV="$(mktemp -d)"
LOG="$(mktemp)"
SERVER_PID=""

cleanup() {
	if [[ -n "$SERVER_PID" ]]; then
		kill "$SERVER_PID" 2>/dev/null || true
		wait "$SERVER_PID" 2>/dev/null || true
	fi
	rm -rf "$VENV" "$LOG"
}
# INT/TERM as well as EXIT: on an untrapped Ctrl-C or a CI cancel/timeout, bash
# exits via the default signal disposition and the EXIT trap is not guaranteed to
# run, leaking the temp venv/log and orphaning the background server. Matches
# dev.sh.
trap cleanup EXIT INT TERM

"$PYTHON" -m venv "$VENV"
"$VENV/bin/pip" install --quiet "$WHEEL"

# --port 0 binds a random free port; the chosen port is only discoverable from
# the "Uvicorn running on ..." line Uvicorn logs to the captured output.
"$VENV/bin/factory-console" --no-browser --host 127.0.0.1 --port 0 >"$LOG" 2>&1 &
SERVER_PID=$!

# Poll (bounded ~10s) for the port; fail fast if the server dies meanwhile.
PORT=""
for _ in $(seq 1 50); do
	if ! kill -0 "$SERVER_PID" 2>/dev/null; then
		echo "smoke: server exited before it was ready" >&2
		cat "$LOG" >&2
		exit 1
	fi
	PORT="$(grep -oE 'Uvicorn running on https?://127\.0\.0\.1:[0-9]+' "$LOG" | grep -oE '[0-9]+$' | tail -n1 || true)"
	if [[ -n "$PORT" ]]; then
		break
	fi
	sleep 0.2
done

if [[ -z "$PORT" ]]; then
	echo "smoke: timed out waiting for the Uvicorn port" >&2
	cat "$LOG" >&2
	exit 1
fi

curl -fsS "http://127.0.0.1:$PORT/api/v1/health"
echo
echo "smoke: OK"
