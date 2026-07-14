# [T35] Tighten CI: unconditional Playwright + coverage gate at 85%

milestone: MVP · track: foundation · depends_on: T33, T18 · provides: `pyproject.toml` coverage gate raised from 0 to 85; `.github/workflows/ci.yml` Playwright step made unconditional (no more presence-check)

## Context

Final MVP gate flip. T18's CI started with the Playwright step conditional on `tests/fixtures/projects/with_run_state/` + `frontend/playwright.config.ts` existing, so early foundation-only PRs still passed. After T33 lands, both artifacts exist at HEAD — the condition is dead. Similarly, pytest was configured with `fail_under=0` so intermediate ticket landings weren't blocked; now that all code is in the tree, tighten to 85% per the architecture testing strategy. Kept as its own ticket because a coverage regression should not be confused with an e2e-harness regression during review.

## Staged approach

1. Edit `pyproject.toml` `[tool.coverage.report]`: `fail_under = 85`.
2. Edit `.github/workflows/ci.yml` Playwright step: remove the `if [ -d ... ]` guard so `pnpm --dir frontend exec playwright install --with-deps chromium && pnpm --dir frontend e2e` always runs.
3. Bump the coverage step to enforce the threshold (`pytest -q --cov=factory_console --cov-report=xml --cov-fail-under=85` — redundant with pyproject `fail_under`, kept explicit for CI-log clarity).
4. Verify locally that `make test` fails if coverage drops below 85% (touch a file to add uncovered code and confirm).

## Critical files

- `pyproject.toml`
- `.github/workflows/ci.yml`

## Interface & data

N/A — CI config + coverage gate.

## Verification

Open a throwaway PR that adds an uncovered function and confirm CI fails on the coverage gate; revert. Confirm the `ci.yml` Playwright step runs unconditionally (check the CI logs). Final MVP CI is fully strict: lint + pytest (85% coverage) + pnpm test + wheel build + smoke + Playwright e2e.
