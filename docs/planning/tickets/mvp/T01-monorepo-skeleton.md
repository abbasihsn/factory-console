# [T01] Monorepo skeleton (dirs, LICENSE, .gitignore, README stub, .python-version)

milestone: MVP · track: foundation · depends_on: none · provides: Empty repo tree matching PROJECT_STRUCTURE.md, ready for track-owned code

## Context

First ticket in the repo. Establishes the directory shape from `docs/planning/PROJECT_STRUCTURE.md` so every subsequent track's PR knows exactly where its files belong. Plants LICENSE, `.gitignore` (must ignore `server/factory_console/_static/` since SPA lands there at package time, plus `node_modules`, `.svelte-kit`, `frontend/build`, `.venv`, `dist`, `build`, `__pycache__`, `*.egg-info`), a stub README pointing at the vision, and the Python version pin. Pure scaffold — no code runs.

## Staged approach

1. Create top-level directories per `PROJECT_STRUCTURE.md`: `server/factory_console/`, `server/factory_console/api/v1/`, `server/factory_console/domain/`, `server/factory_console/services/`, `server/factory_console/file_adapter/`, `frontend/`, `tests/unit/`, `tests/integration/`, `tests/fixtures/projects/`, `docs/planning/tickets/mvp/`, `scripts/`, `.github/workflows/`.
2. Add `.gitkeep` files where needed so git tracks otherwise-empty dirs.
3. Write `LICENSE` (MIT).
4. Write `.gitignore`.
5. Write `.python-version` pinning `3.11`.
6. Write `README.md` stub with a one-paragraph pitch from `VISION.md` and a "quickstart to come" marker.

## Critical files

- `LICENSE`
- `.gitignore`
- `.python-version`
- `README.md`
- `server/factory_console/.gitkeep`
- `frontend/.gitkeep`
- `tests/fixtures/projects/.gitkeep`
- `docs/planning/tickets/mvp/.gitkeep`
- `scripts/.gitkeep`
- `.github/workflows/.gitkeep`

## Interface & data

N/A — scaffold, no external interface.

## Verification

`git status` shows the expected tree; `find . -type d | sort` matches `PROJECT_STRUCTURE.md`.
