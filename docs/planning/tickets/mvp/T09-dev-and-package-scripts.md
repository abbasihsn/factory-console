# [T09] Dev + package scripts + Makefile (SPA-into-wheel packaging)

milestone: MVP · track: foundation · depends_on: T02, T03, T06 · provides: `make dev/test/build/package/smoke/release` loop; `scripts/dev.sh` (Uvicorn `--reload` + Vite dev with proxy); `scripts/package.sh` (SPA → `_static/` → wheel)

## Context

Turns the toolchain into muscle memory for contributors and for CI. `scripts/dev.sh` is the hot-reload loop; `scripts/package.sh` is the reproducible packaging recipe called by CI's release job.

## Staged approach

1. `scripts/dev.sh` (bash `set -euo pipefail`): pick `PY_PORT` (default 8000) + `FE_PORT` (default 5173); start `uvicorn factory_console.app:create_app --factory --reload --port $PY_PORT --host 127.0.0.1` in background, capture PID; `cd frontend && pnpm dev --port $FE_PORT` (foreground); `trap EXIT` to kill Uvicorn PID. **Note**: T20 will replace the target with `factory_console.app:create_dev_app` when `create_app` gains required args.
2. `scripts/package.sh` (bash `set -euo pipefail`): `cd frontend && pnpm install --frozen-lockfile && pnpm build`; `rm -rf ../server/factory_console/_static && mkdir -p ../server/factory_console/_static && cp -R build/* ../server/factory_console/_static/`; `cd .. && python -m build --wheel --sdist`.
3. `chmod +x` both.
4. `Makefile` with `.PHONY` targets: `dev`, `test` (`pytest -q && cd frontend && pnpm test`), `lint` (`ruff check . && ruff format --check . && cd frontend && pnpm lint`), `build` (`python -m build --wheel`), `package` (`scripts/package.sh`), `smoke` (installs the built wheel in a tmp venv, boots `--no-browser --port 0`, curls `/health`, kills PID), `release` (echoes "push a v* tag"), `clean` (`rm -rf build dist *.egg-info server/factory_console/_static frontend/build frontend/.svelte-kit`).

## Critical files

- `scripts/dev.sh`
- `scripts/package.sh`
- `Makefile`

## Interface & data

Implements packaging contract from `ARCHITECTURE.md` devops: wheel bundles the SPA under `factory_console/_static/`. Dev-loop: Vite proxies `/api/*` to Uvicorn (same-origin in prod).

## Verification

`make dev` boots both; hitting `http://127.0.0.1:5173/api/v1/health` reaches Python `/health` via proxy; `make package` produces `dist/*.whl` containing `factory_console/_static/index.html`; `make smoke` installs the wheel in a tmp venv, boots, curls `/health`, kills — exits 0; `make clean` removes artifacts.
