# [T05] Pre-commit config (ruff + ruff-format + eslint + prettier)

milestone: MVP · track: foundation · depends_on: T02, T03 · provides: Pre-commit hook config that runs ruff + ruff-format on Python and eslint + prettier on frontend; installable via `pre-commit install`; also invoked in CI for parity

## Context

Blocks style noise before it reaches review. Reuses configs already declared in `pyproject.toml` (T02) and `frontend/.eslintrc` + `.prettierrc` (T03). CI re-runs pre-commit to catch anyone who bypassed the local hook.

## Staged approach

1. `.pre-commit-config.yaml`:
   - `astral-sh/ruff-pre-commit` (ruff + ruff-format hooks pinned, files `^(server|tests)/.*\.py$`).
   - `pre-commit/pre-commit-hooks` (trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files).
   - local repo running `pnpm --dir frontend lint` on `frontend/**/*.{ts,svelte,js,html,css}` as a system hook.
   - local repo running `pnpm --dir frontend format:check`.
2. Add scripts to `frontend/package.json`: `lint='eslint . && prettier --check .'`, `format='prettier --write .'`, `format:check='prettier --check .'`.
3. Note in README (or T19's contributing.md) that `pre-commit install` is part of onboarding.

## Critical files

- `.pre-commit-config.yaml`
- `frontend/package.json`

## Interface & data

N/A — scaffold, no external interface.

## Verification

`pre-commit install` succeeds; `pre-commit run --all-files` runs both stacks and exits 0 on the current tree; a deliberately unformatted `.py` or `.svelte` file is caught.
