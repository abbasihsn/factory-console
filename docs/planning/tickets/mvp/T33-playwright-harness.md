# [T33] Playwright e2e harness (config + global setup/teardown + happy-path spec)

milestone: MVP · track: frontend · depends_on: T27, T28, T29, T30, T31, T32, T25, T08 · provides: Playwright config + global-setup/teardown that boots the packaged `factory-console` on the `with_run_state` fixture + `happy-path.spec.ts` — the MVP acceptance harness

## Context

The MVP acceptance harness per the vision's success criteria. Boots the real packaged `factory-console` on `tests/fixtures/projects/with_run_state/` with `--no-browser --port 0`, parses printed port from stdout, points Playwright at that URL. Drives the full happy path: list -> filter -> click ticket -> see body + badges + deps -> click a dep -> land on that ticket. Screenshots pipeline (T34) and the CI/coverage-gate tightening (T35) split out — this ticket ships ONLY the harness + one spec so review stays scoped.

## Staged approach

1. Add `@playwright/test` to `frontend/package.json` devDeps (already added in T03; confirm) + `e2e` script (`playwright test`).
2. `frontend/playwright.config.ts` — `use.baseURL` reads from `FC_E2E_BASE_URL`; `globalSetup` + `globalTeardown` paths.
3. `frontend/tests/e2e/global-setup.ts`: spawns `factory-console <abs-path-to-with_run_state-fixture> --no-browser --port 0`, tails stdout until match of `http://127.0.0.1:(\d+)`, exports `process.env.FC_E2E_BASE_URL`, stores child PID in a temp file for teardown.
4. `frontend/tests/e2e/global-teardown.ts` reads PID + kills (SIGTERM, then SIGKILL fallback).
5. `frontend/tests/e2e/happy-path.spec.ts`:
   - (a) `/` -> at least one `TicketRow` with expected fixture id;
   - (b) type substring in search box -> list narrows;
   - (c) click a known row -> URL is `/tickets/<id>`, badges + `MarkdownBody` content present;
   - (d) click "View dep neighborhood" link -> `/deps` shows expected direct dep;
   - (e) click a dep mini-row -> lands on that ticket's detail page.

## Critical files

- `frontend/package.json`
- `frontend/playwright.config.ts`
- `frontend/tests/e2e/global-setup.ts`
- `frontend/tests/e2e/global-teardown.ts`
- `frontend/tests/e2e/happy-path.spec.ts`

## Interface & data

Consumes REST v1 surface transitively via SPA. Consumes CLI contract stdout URL line (`'Factory Console vX.Y.Z — serving <root> at http://127.0.0.1:<port>'`) for port parsing in `global-setup`. Consumes `with_run_state` fixture (read-only).

## Verification

Locally: `make package && pipx install ./dist/*.whl && pnpm --dir frontend e2e` runs the harness green; kill the process mid-run and confirm no orphaned `factory-console` via `ps -ef | grep factory-console`. Manually confirm the happy-path spec traverses all five stages.
