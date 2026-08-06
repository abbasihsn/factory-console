# [T140] PR status client + `PrStatusBadge`, surfaced on ticket detail

milestone: v3.0.1 · track: frontend · depends_on: T139, T121, T31, T102 · provides: `lib/api/pullRequests.ts` + a `PrStatusBadge` that renders a PR state and link OR the named reason there is none, with its first consumer on the ticket detail page — fetched client-side, outside the `invalidateAll()` refresh path.

## Context

v3.0.1 adds a read-only `GitHubAdapter`, and its whole value is visible only if the console shows the
PR. This ticket adds the typed client for it and the one component that renders a PR answer, then
places it where a user asks the question most directly.

**The rule the component exists to enforce** is the console's standing one: `gh` absent, wrong
account, not a git repo, no GitHub remote, rate-limited and timed out are DIFFERENT things to say, and
none of them is "no PR". The server discloses only named fields out of `gh`'s JSON (the disclosure
rule binds it), so this component reads only what the schema publishes and treats anything missing as
missing.

**The fetch must NOT live in the route's `load` function.** `+layout.svelte` calls `invalidateAll()`
on every SSE bump, which re-runs every load in the current route. During an active factory run — the
exact moment the console is being watched, and when `.factory/results/*` and `run-state.json` churn —
that would spawn a fresh, uncached 1–6 s `gh` invocation per change event, and would make ticket
detail (an instant page today) block for seconds. So the PR read is fetched client-side after mount
into a local store, rendered in a pending state until it resolves, and explicitly excluded from the
invalidation path, with at most one request in flight.

## Staged approach

1. Regenerate `src/lib/api/types.ts` (`pnpm --dir frontend codegen` against a server carrying T139's
   endpoint); add its friendly aliases to `src/lib/api/models.ts`, each straight from
   `components['schemas'][...]`.
2. CREATE `src/lib/api/pullRequests.ts` in the `runs.ts` / `spend.ts` idiom — one read wrapper over the
   shared `request()`, envelope-unwrapped — with a header comment stating that the response's
   project-level `source` block is what distinguishes "no PR" from "could not ask", and that callers
   MUST branch on it. Re-export from the barrel `src/lib/api/index.ts`.
3. CREATE `src/lib/components/PrStatusBadge.svelte`: presentational, props `{ pr, reason }`. Renders
   the PR number/state as a badge linking to the PR URL — **reusing `/runs`' existing URL guard
   discipline**: render a link only for an `http(s)` URL with no userinfo, using the NORMALIZED
   `URL.href`, else a "PR url this console will not link" pill. State labels come from an exhaustive
   `Record<PullRequestState, …>` keyed on the generated union so a new state is a compile error
   (`RunStateBadge`'s rule). A null entry renders the named reason from the source block, not a blank.
   A `pending` state renders while the fetch is in flight, so an unresolved read never looks like an
   answer.
4. CREATE a small client-side store (or a local `$effect` in the detail page) that fetches the PR data
   after mount, holds `pending | loaded | failed`, and **does not subscribe to the live-store bump**.
   Debounce to at most one in-flight request. Document the exclusion and cross-reference T139's
   flagged trade-off.
5. `src/routes/tickets/[id]/+page.svelte`: render `<PrStatusBadge>` in the ticket header beside the
   existing status/run-state badges, fed from that store. **`+page.ts` is NOT changed** — the ticket
   page must still render instantly and must never fail because GitHub could not be asked.
6. Tests: CREATE `src/lib/api/pullRequests.test.ts` and `src/lib/components/PrStatusBadge.test.ts`
   (each degraded reason renders its own text; an unsafe url is refused; a good url links; the pending
   state renders). Extend `src/routes/tickets/[id]/page.test.ts` for the rendered badge and for the
   non-fatal failure path, and assert the PR fetch is not re-issued on an `invalidateAll()`.

## Critical files

- `frontend/src/lib/api/types.ts` (regenerate — aggregation file)
- `frontend/src/lib/api/models.ts` (modify — aggregation file)
- `frontend/src/lib/api/pullRequests.ts` (create)
- `frontend/src/lib/api/index.ts` (modify — aggregation file)
- `frontend/src/lib/components/PrStatusBadge.svelte` (create)
- `frontend/src/routes/tickets/[id]/+page.svelte` (modify)
- `frontend/src/lib/api/pullRequests.test.ts` (create)
- `frontend/src/lib/components/PrStatusBadge.test.ts` (create)
- `frontend/src/routes/tickets/[id]/page.test.ts` (modify)

## Interface & data

A read wrapper over T139's endpoint (path from the OpenAPI document, not redefined), returning
per-ticket PR entries plus the project-level `source` block in the console's `found` / `read` /
`reason` vocabulary; `PrStatusBadge` props `{ pr: PullRequestRef | null; reason?: GitHubEntryReason;
pending?: boolean }`.

Contracts by reference: ARCHITECTURE.md "v3 → GitHubAdapter (NEW, read-only)"; the disclosure rule
under "Other factory artefacts" (the wire carries only declared fields out of `gh` JSON — this view
never asks for more); REST v1's error envelope; T131's vocabulary.

DB ops: none. NFR flags: **no client-side caching of PR data** (v3.4 owns PR caching — do not
pre-empt it) but also **no re-fetch on SSE invalidation**; untrusted-URL guard on every rendered
href; a GitHub failure is non-fatal to the page.

Aggregation note: `types.ts`, `models.ts` and `index.ts` are shared with T121, which this ticket
depends on, so they serialize.

## Verification

`pnpm --dir frontend test`, `pnpm --dir frontend check`, `pnpm --dir frontend lint`, `make lint`.
Manual via `./scripts/dev.sh`: on a project WITH a GitHub remote the badge links the PR; on
`tests/fixtures/projects/minimal` (no remote) it names that condition; with `gh` renamed off `PATH` it
names THAT condition instead — three different sentences, and the ticket page still renders instantly
in all three. Then touch a watched file repeatedly and confirm **no additional `gh` invocations** in
the server log.
