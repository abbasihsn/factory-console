# [T74] Editing e2e (part 1): extend mutable-console harness with write-token + create/edit/diff-preview/save flow

milestone: v2 · track: testing · depends_on: T33, T53, T64, T65, T70, T71 · provides: an extended dedicated-console harness that surfaces the stderr-printed per-session write token, plus a Playwright spec driving create → edit → diff-preview → save → confirm against a disposable fixture copy.

## Context

v2's editing UI is the user-facing payoff and needs a real-browser acceptance harness. The existing T53 dedicated-console helper already copies a fixture to a private temp dir, boots a dedicated `--port 0` console, captures stderr, and disposes cleanly — the ideal base for write e2e because it isolates mutations from the shared read-only fixtures/console. This ticket extends that helper to expose the write token the console prints to stderr (so the browser session can be authorized like the real SPA) and adds the primary happy-path editing spec.

## Staged approach

1. In `frontend/tests/e2e/lib/dedicated-console.ts` (MODIFY — the shared harness helper; the ONLY e2e ticket that edits it), parse the write token out of the already-captured `stderr` buffer (the console prints `X-Factory-Write-Token: <token>` per T64) and expose it on the `DedicatedConsole` interface (e.g. `readonly writeToken: string`); keep the existing `moveRunState`/`dispose`/`baseURL` API intact and backward-compatible. Do NOT re-export from any barrel.
2. Create `frontend/tests/e2e/editing.spec.ts` using `start()`.
3. Navigate to the dedicated console by its own `baseURL`, open a todo ticket (e.g. CAD-140), enter the edit form, modify the body, open the diff-preview modal and assert the diff renders the change, click save+confirm, and assert the change is persisted (row/detail re-renders with the new content).
4. Add a create-ticket case: use the SPA's create affordance to add a new todo ticket and assert it appears in the list.
5. Follow `live-update.spec.ts`'s beforeAll/afterAll(dispose) + guarded-handle pattern; keep the spec focused so the PR stays simple.

## Critical files

- `frontend/tests/e2e/lib/dedicated-console.ts`
- `frontend/tests/e2e/editing.spec.ts` (new)

## Interface & data

Harness surface extended (by reference to T53's `DedicatedConsole`): add `writeToken` sourced from the console's stderr line (per T64's stderr contract). UI under test (by reference): the v2 edit form + diff-preview modal + save/confirm (T70) and create route (T71). Endpoints exercised indirectly: `POST`/`PUT /api/v1/tickets` with the `X-Factory-Write-Token` header (T65). Entities: `Ticket`, `RunState`. No DB — writes land only in the disposable temp fixture copy owned by the harness. NFR: auth (write token), idempotency (confirm-before-save).

## Verification

`pnpm --dir frontend exec playwright install --with-deps chromium` then `pnpm --dir frontend e2e` (or target `editing.spec.ts`). Locally, boot from source via `FC_E2E_CONSOLE_CMD` as the harness supports. No shared fixture is mutated — only the harness's temp copy.
