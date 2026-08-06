# [T127] Multi-project e2e — switch, deep-link, add and remove in a real browser

milestone: v3.0 · track: testing · depends_on: T120, T122, T124, T125, T126, T08 · provides: the acceptance harness for v3.0 — a real browser against a real two-project console, proving the switch, the deep-link-after-switch answer, add/remove, and that the live stream recovers.

## Context

Every prior milestone's user-visible claim was pinned by a Playwright spec against the real packaged
CLI, and v3.0's claim — "the console shows one project at a time and you can switch which one" — is
only true if a browser can be driven through it.

This spec is also where the decisions the frontend track made get **proven rather than asserted**:
what a deep link into a ticket that does not exist in the newly-selected project does, that switching
does not leave the SSE indicator stuck, that a degraded project says so, and that the boot-time PATH
still wins as the initial selection (T111's precedence rule).

## Staged approach

1. CREATE `frontend/tests/e2e/multi-project.spec.ts`, using
   `startMulti(['with_run_state', 'minimal'])` from T120's dedicated-console helper in `beforeAll`
   and `dispose()` in `afterAll`. The shared global-setup console stays single-project and read-only —
   this spec is the sole writer of its own registry, exactly as `live-update.spec.ts` is the sole
   writer of its own fixture copy.
2. Steps, each gated on a web-first assertion and role/label locators (**no `networkidle`** — the SSE
   stream never lets the network idle, the constraint the existing specs already document):
   - the switcher lists both projects;
   - switching from `/` re-renders the ticket list with the other project's ids;
   - switching from `/graph` STAYS on `/graph` with different contents;
   - switching while on `/tickets/<id>` lands on `/`;
   - a hand-typed deep link to a ticket id that exists only in the other project renders the existing
     named not-found panel — never a blank page or a stack trace.
3. **Precedence (T111):** boot with a PATH while the persisted selection names the other project, and
   assert the console serves the PATH — then switch, and assert the switch takes effect in the same
   session.
4. `/projects`: seed the write token into `sessionStorage` under the SPA's key the way
   `editing.spec.ts` does, then register a third fixture copy through the form and see it appear in
   the switcher; submit a bogus path and assert a NAMED refusal (assert on the specific message, not
   merely that some error appeared); remove a project behind the confirm dialog and see it leave the
   switcher. Cover the 401 path: clear the token and confirm the prompt appears rather than a silent
   failure.
5. Assert the `minimal` fixture (no `.factory/`) renders its named condition in the shell banner
   rather than an empty `/runs` table.
6. Assert `LiveIndicator` stays on "Live" across a switch (no `disconnected` flash — T126).

## Critical files

- `frontend/tests/e2e/multi-project.spec.ts` (create)

## Interface & data

N/A — a Playwright spec. It exercises, by reference: the REST v1 registry endpoints (T112/T113),
`GET /api/v1/project`, `GET /api/v1/tickets`, `GET /api/v1/events`, and the CLI contract's boot line.

Fixtures used as contract: `tests/fixtures/projects/with_run_state` (populated — run-state markers)
and `tests/fixtures/projects/minimal` (deliberately bare) — chosen because the two DIFFER in exactly
the conditions this milestone must name. `tests/fixtures/projects/second` (T119) is available as the
third.

NFR flags: single-worker/serial as the Playwright config already pins; the spec owns its temp copies
and disposes them; it must never touch `~/.factory-console/` (guaranteed by T120).

## Verification

`pnpm --dir frontend exec playwright test multi-project` locally against a from-source console
(`FC_E2E_CONSOLE_CMD='python3 -m factory_console'` with `PYTHONPATH=server`), then the full
`pnpm --dir frontend exec playwright test` to confirm no interference with the shared console.
`make smoke` to confirm the packaged wheel serves the same SPA the spec drove.
