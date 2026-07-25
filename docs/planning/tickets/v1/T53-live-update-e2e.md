# [T53] Live-update e2e spec + dedicated mutable-console harness helper (watcher/SSE view refresh)

milestone: v1 · track: testing · depends_on: T50, T45, T44, T33, T08 · provides: deterministic e2e proof that a watcher-detected fixture mutation refreshes the open view via SSE, plus a reusable copy-to-temp dedicated-console harness helper

## Context

The hardest v1 e2e: with the watcher running against a project, mutating a file (or run-state) must refresh the open view via the `/api/v1/events` SSE stream. The shared fixtures are contractually read-only and the shared console is single-worker, so this test cannot mutate them in place. It therefore adds a small, self-contained harness helper that copies `with_run_state` into an OS temp dir and boots a DEDICATED second console (watcher on) against the copy, mutates the copy, asserts the refresh, and disposes everything — leaving the shared fixture/console untouched. This is where the v1 watcher (the deliberate extension over the watcher-free MVP) is verified end to end. The helper is reusable by future live tests.

## Staged approach

1. Create `frontend/tests/e2e/lib/dedicated-console.ts` (a `lib/` file — NOT matched by Playwright's `*.spec` test glob, so never run as a test). It exports a helper that: (a) recursively copies `tests/fixtures/projects/with_run_state` into a fresh `os.mkdtemp` dir (`fs.cpSync` recursive — carries both file and directory run-state markers); (b) resolves the launcher INDEPENDENTLY from `FC_E2E_CONSOLE_CMD` (default `factory-console`) so it does NOT import or modify `global-setup.ts`; (c) spawns the console with `[tempDir, '--no-browser', '--port', '0']`, parses the `http://127.0.0.1:<port>` URL from stdout with a bounded boot timeout (mirroring `global-setup`'s URL pattern); (d) exposes mutate helpers (e.g. `moveRunState(id, from, to)` via `fs.renameSync` on the copy) and a `dispose()` that SIGTERMs the child, polls to SIGKILL like `global-teardown`, then `rmSync`s the temp dir.
2. Create `frontend/tests/e2e/live-update.spec.ts`: in `test.beforeAll`, start the dedicated console and capture its baseURL + tempDir; in `test.afterAll`, dispose.
3. In the test: `page.goto('${dedicatedBaseURL}/')` (absolute URL — NOT `use.baseURL`, which points at the shared console); assert CAD-140's run-state badge reads "To do" (initial state) so the view is mounted.
4. DETERMINISM GATE: before mutating, wait for the SSE connection to be established — `page.waitForResponse` matching `**/api/v1/events` (or the frontend's connected/live indicator) — closing the connect-vs-mutate race.
5. Mutate the copy: `moveRunState('CAD-140', 'todo', 'in-flight')` (rename the marker under the copy's `.factory/run-state/`).
6. Assert refresh with a BOUNDED web-first poll: `expect(CAD-140's run-state badge).toHaveText('In flight', { timeout: ~10s })` — the retry IS the bounded wait; no fixed sleep.
7. Keep the mutation single and the test the sole writer.

## Critical files

- `frontend/tests/e2e/lib/dedicated-console.ts` (new — reusable harness helper, not a spec)
- `frontend/tests/e2e/live-update.spec.ts` (new)

## Interface & data

- Consumes (by reference): the `GET /api/v1/events` SSE contract (T45; the watcher watches `docs/planning/**` + `.factory/run-state/**` per T40), the T50 frontend live-update SSE client, the `RunState` enum, and the CLI contract (`factory-console [PATH] --no-browser --port 0` prints exactly one `http://127.0.0.1:<port>` stdout line). Helper: `start(fixtureName?) -> { baseURL, tempDir, moveRunState(id, from, to), dispose() }`.
- DB ops: N/A (filesystem mutation on a disposable copy). NFR: DETERMINISM is the headline — isolated temp copy (sole writer), SSE-connection-established gate before mutation, web-first bounded-retry assertion (no sleeps), ephemeral `--port 0` (no port clash with the shared console); process hygiene (SIGTERM→poll→SIGKILL + temp-dir cleanup on teardown, even on setup failure); auth N/A. The shared read-only fixtures and shared console are never mutated.

## Verification

From `frontend/`: `pnpm run e2e -- tests/e2e/live-update.spec.ts`. Set `FC_E2E_CONSOLE_CMD` for from-source runs (e.g. `PYTHONPATH=server python3 -m factory_console`) so the helper's dedicated child boots the in-repo package with the v1 watcher enabled. CI runs it via the T35 Playwright step. Green = the open list shows CAD-140 "To do", the `/api/v1/events` stream connects, moving its run-state marker on the temp copy flips the badge to "In flight" within the bounded wait, and `afterAll` leaves no orphaned console or temp dir. Confirm the shared `with_run_state` fixture is unchanged after the run.
