# [T18] CI workflow (matrix lint + pytest + pnpm test + build + smoke; conditional e2e)

milestone: MVP · track: foundation · depends_on: T02, T03, T05, T06, T09 · provides: GitHub Actions `ci.yml` enforcing lint + tests + wheel build + smoke-install across `{ubuntu-latest, macos-latest} x Python {3.11, 3.12}`. Playwright e2e conditional on `tests/fixtures/projects/with_run_state/` existing

## Context

Merge gate for every track. Matrix covers cross-platform NFR. Smoke test proves the wheel is runnable end-to-end, catching packaging regressions before release. Playwright e2e is conditional so foundation-only PRs still pass; once T08 fixtures + T33 e2e specs land, it always runs (T35 flips the gate).

## Staged approach

1. `.github/workflows/ci.yml`. `name: CI`. `on: {push: {branches: [main]}, pull_request: {}}`. `concurrency: {group: ci-${{ github.ref }}, cancel-in-progress: true}`. `jobs.test: strategy.matrix: {os: [ubuntu-latest, macos-latest], python: ['3.11','3.12']}; runs-on: ${{ matrix.os }}`. Steps:
   - checkout;
   - `setup-python` (matrix) with `cache=pip`;
   - `pnpm/action-setup@v3`;
   - `setup-node@v4` `node-version=20` with `cache=pnpm`;
   - `pip install -e .[dev]`;
   - `pnpm --dir frontend install --frozen-lockfile`;
   - `pre-commit run --all-files`;
   - `pytest -q --cov=factory_console --cov-report=xml` (no threshold yet; T35 flips to 85%);
   - `pnpm --dir frontend test`;
   - `bash scripts/package.sh`;
   - `python -m venv /tmp/smokevenv && /tmp/smokevenv/bin/pip install dist/*.whl`;
   - small helper script boots `factory-console --no-browser --port 0`, parses URL from stdout, curls `/api/v1/health`, asserts shape, kills PID;
   - conditional Playwright step: `if [ -d tests/fixtures/projects/with_run_state ] && [ -f frontend/playwright.config.ts ]; then pnpm --dir frontend exec playwright install --with-deps chromium && pnpm --dir frontend e2e; fi`.
2. Upload `coverage.xml` artifact.
3. Add CI status badge to `README.md`.

## Critical files

- `.github/workflows/ci.yml`
- `README.md`

## Interface & data

Consumes CLI contract (smoke) and REST v1 `/health`. Consumes packaging contract from T09.

## Verification

Push a branch: CI green on all 4 matrix cells; break a ruff rule -> lint fails; break `/health` -> smoke fails; break a Vitest test -> `pnpm test` fails; concurrency cancels stale runs.
