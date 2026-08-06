# [T122] Project switcher in the TopBar, over a registry-aware layout load

milestone: v3.0 · track: frontend · depends_on: T121, T111, T27, T49 · provides: v3.0's headline — a dropdown in the TopBar listing registered projects that switches the server-side selection and correctly re-loads whatever route the user is on.

## Context

The console must show one project at a time and let the user switch which one. The selection lives on
the server (single-user), so the shell must READ it rather than own it: the layout load already
fetches `GET /api/v1/project` for the TopBar, and extending it is the natural seam. Keeping no
authoritative client copy is what keeps the SPA honest across tabs and reloads — another tab's switch
is observed on the next load, invalidation or SSE bump, and nothing is persisted client-side (unlike
`stores/writeToken.ts`, which persists a secret precisely because the server does not hold it).

This ticket decides and encodes the two behavioural questions the switch raises — what happens to a
route whose URL embeds a ticket id, and what happens to loads already in flight — and it must leave
`factory-console PATH` on a single project looking exactly as it does today.

## Staged approach

1. `src/routes/+layout.ts`: alongside the existing `/api/v1/project` fetch (whose failure policy is
   unchanged and stays the only fatal one), fetch the registry list and the current selection via
   `$lib/api` in PARALLEL with `Promise.all`. **A registry read that FAILS must not blank the
   shell**: catch it and return `projects: []`, so the console degrades to today's single-project
   shell. Return `{ project, projects, selectedId }`.
2. CREATE `src/lib/projects/switchTarget.ts` — one pure function
   `switchTarget(pathname: string): string | null` returning `/` for a route whose URL embeds a
   ticket id (`/tickets/<id>`, `/tickets/<id>/deps`) and `null` (= stay, just invalidate) for every
   other route, with a comment stating why: a ticket id is a fact about ONE project's manifest, so
   carrying it across a switch would deep-link into a ticket that is legitimately not there. Note
   that a hand-typed deep link is deliberately NOT redirected — the detail loader's existing 404 →
   `notFound` panel already names that case.
3. CREATE `src/lib/components/ProjectSwitcher.svelte`. Like `NavSearch` (and unlike the prop-only
   `TopBar`), it OWNS its action, so `TopBar` stays `$app`-free. Props `{ projects, selectedId }`; on
   change it sets a local `busy` (control `disabled` + `aria-busy`) so a second switch cannot race the
   first, `await`s `selectProject(id, token)`, then routes via `switchTarget` —
   `goto(target, { invalidateAll: true })` when it returns a path, else `invalidateAll()`.
   **Awaiting the write BEFORE invalidating** is what stops a load being issued against an
   uncommitted selection; SvelteKit discards superseded loads, so an in-flight read for the old
   project cannot paint over the new one. On an `ApiError` with `code === WRITE_TOKEN_INVALID_CODE`,
   `clearToken()` and render the existing `WriteTokenPrompt` inline, then retry; on any other
   `ApiError`, render `ApiErrorView` with `compact` and `actionLabel="Try again"`. Render NOTHING
   (return early) when there is no registry and fewer than two projects, so single-project mode is
   visually unchanged.
4. `src/lib/components/TopBar.svelte`: accept the new `projects` / `selectedId` props and slot
   `<ProjectSwitcher>` next to the root path; keep the component presentational.
5. `src/routes/+layout.svelte`: pass `data.projects` / `data.selectedId` through to `TopBar`.
6. Tests: extend `src/routes/layout.test.ts` for the new load (registry present; registry read
   failing → empty list + shell still resolves); CREATE `src/lib/projects/switchTarget.test.ts`
   (every existing route path); CREATE `src/lib/components/ProjectSwitcher.test.ts` (mock
   `$app/navigation` and `$lib/api` as `NavSearch.test.ts` mocks `goto`) — the select lists the
   projects, a change calls `selectProject` then the right navigation, it is inert while busy, and a
   401 raises the token prompt.

## Critical files

- `frontend/src/routes/+layout.ts` (modify)
- `frontend/src/routes/+layout.svelte` (modify — aggregation file)
- `frontend/src/lib/components/TopBar.svelte` (modify)
- `frontend/src/lib/components/ProjectSwitcher.svelte` (create — aggregation file)
- `frontend/src/lib/projects/switchTarget.ts` (create)
- `frontend/src/routes/layout.test.ts` (modify)
- `frontend/src/lib/components/ProjectSwitcher.test.ts` (create)
- `frontend/src/lib/projects/switchTarget.test.ts` (create)

## Interface & data

`LayoutData` widens from `{ project }` to
`{ project: Project; projects: RegisteredProjectOut[]; selectedId: string | null }`.
`ProjectSwitcher` props `{ projects, selectedId }`. `switchTarget(pathname: string): string | null`.

Contracts by reference: REST v1 `GET /api/v1/project` (unchanged); the registry + selection endpoints
via `$lib/api` (T121); the v2 write-token scheme for the selection mutation (T113).

DB ops: none. NFR flags: auth = write token on the selection call, with the `write_token_invalid` 401
handled by the existing prompt; idempotency = re-selecting the current project is a no-op the
switcher short-circuits; concurrency = one switch at a time via `busy`.

Aggregation note: `+layout.svelte` is also touched by T125 and T126, and `ProjectSwitcher.svelte` by
T123 — all of which depend on this ticket, so they serialize.

## Verification

`pnpm --dir frontend test`, `pnpm --dir frontend check`, `pnpm --dir frontend lint`.
Manual: `./scripts/dev.sh`, register two projects, switch from `/graph` (stays on `/graph`, contents
change) and from `/tickets/<id>` (lands on `/`). Then `make lint`, and `make smoke` to confirm the
packaged wheel's SPA still boots on a single project with no switcher.
