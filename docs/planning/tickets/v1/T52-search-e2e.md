# [T52] Search e2e spec: global search box returns cross-ticket full-text results, links, and empty state

milestone: v1 · track: testing · depends_on: T49, T41, T33, T08 · provides: e2e coverage that the global search box surfaces cross-ticket full-text matches that link to their tickets, and that a no-match query shows the empty state

## Context

v1 adds cross-ticket full-text search (a global search box + `GET /api/v1/search`) — a step up from the MVP list route's server-side id/title filter. This spec is the acceptance for that slice: it proves search matches on BODY text (not just titles), that results link correctly, and that the no-match/empty state renders. It reuses the shared read-only console on `with_run_state`, whose bodies contain distinctive body-only terms (e.g. "idempotent" in CAD-125's body but not its title), letting the test prove the match is genuinely full-text.

## Staged approach

1. Create `frontend/tests/e2e/search.spec.ts`.
2. Reuse the shared console via `use.baseURL` (no new instance; do not modify `global-setup.ts` / `playwright.config.ts`).
3. `page.goto('/')`; locate the global search box by role `searchbox` / accessible name.
4. Full-text case: fill a BODY-only term ("idempotent", which appears in CAD-125's body but not any title); assert a result entry for CAD-125 appears (proving full-text, not title-only) and that it is a link to `/tickets/CAD-125`.
5. Click the result and `expect(page).toHaveURL(/\/tickets\/CAD-125$/)`; assert the detail heading is visible.
6. Multi-match case (optional, cheap): fill a term shared by several tickets and assert more than one result.
7. Empty-state case: fill a gibberish term ("zzznomatchqqq"); assert zero result links and that the explicit no-results/empty message is visible.
8. Role/label locators + web-first assertions only (the box is debounced; retrying assertions absorb it) — no fixed sleeps.

## Critical files

- `frontend/tests/e2e/search.spec.ts` (new — the only file)

## Interface & data

- Consumes (by reference): the `GET /api/v1/search` response contract (T41) and the T49 global search box + results; navigation targets `/tickets/{id}`.
- Determinism note: use a body-only term guaranteed present in the fixture (CAD-125 body: "idempotent") so the match is stable and proves full-text; use a gibberish term guaranteed absent for the empty state.
- DB ops: N/A. NFR: determinism (web-first retry absorbs the search-box debounce; no sleeps); shared single-worker serial config, read-only fixture — no mutation; auth N/A.

## Verification

From `frontend/`: `pnpm run e2e -- tests/e2e/search.spec.ts`. CI runs it via the T35 Playwright step. Use `FC_E2E_CONSOLE_CMD` for from-source runs. Green = "idempotent" surfaces CAD-125 via body match, the result links to its detail, and "zzznomatchqqq" shows the empty state.
