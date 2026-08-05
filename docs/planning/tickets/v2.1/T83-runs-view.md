# [T83] `/runs` — the run view

milestone: v2.1 · track: frontend · depends_on: T81 · provides: a `/runs` route listing every ticket with its run-state, PR link and lane outcome, rendering the no-run-data case as an explicit state rather than an empty table.

> **SHIPPED, AND THIS TEXT IS NOT WHAT SHIPPED.** Recorded 2026-08-05 by the v2.1 integration audit
> (finding F6), narrowed in place per T93's precedent. T83 merged as PR #195; the header above still
> says `depends_on: T81`, and T81 no longer exists — it was split into T88/T89/T90 and is
> `superseded` in `tickets.json`, which repointed T83's dependency to **T90**.
>
> The contract below is T81's, and **T90 does not ship it**. What shipped is
> `server/factory_console/api/v1/runs.py` returning `{items, total}` of
> `RunRecord{ticketId, result, receipt}` — with no project-level `sources` block, no `prUrl` field,
> no per-row `unavailable` list, and no `getRun(id)`. T83's implementation derived the all-absent
> conclusion per-record instead and documented why in `SourcesBanner.svelte`.
>
> Left as-is, sections 1-4 below read as unbuilt requirements against a merged Ticket, which is the
> one thing a plan must never do: **the spec outlived the contract it was written against**, and a
> reader cannot tell that from the text. The paragraphs are kept rather than rewritten because they
> record what was intended and why the divergence happened; they are not a description of the code.

## Context

`GET /api/v1/runs` (T81) returns per-ticket run records plus a `sources` block saying which factory artefacts were found. This is the view. It follows the existing route shape — `+page.ts` loader calling `$lib/api`, `+page.svelte` rendering, `page.test.ts` with the api module mocked — the same pattern `/graph`, `/roadmap` and `/search` already use, so there is no new architecture here.

The design constraint is the one T81 was built for. `.factory/` is gitignored, so **the common case for anyone who clones this repository is that there is no run data at all.** A table with 77 rows and empty cells reads as "the factory ran and recorded nothing". The view must instead say the sources were not found, and where it looked. `sources[*].found == false` drives that; `runs` being empty does not, because those are different situations and only the first one is knowable from the runs list alone.

T78 widens `RunState` to nine-plus members including `flagged`, `failed` and `needs_human`, and T80 adds `absent`. The badge and any per-state grouping in this view must be exhaustive over the current union at build time, not over the five states the console used to have — a stale branch set is the exact defect class this milestone exists to fix, and reintroducing it here would be the milestone failing on its own terms.

## Staged approach

1. Add `frontend/src/lib/api/runs.ts`: `getRuns()` and `getRun(id)` against the generated OpenAPI types, matching the existing api-module conventions (error envelope via `ApiError`, no bare `fetch` in components).
2. Add `frontend/src/routes/runs/+page.ts` loading `getRuns()`, and `+page.svelte` rendering: a `SourcesBanner` when any source is missing, then a table of ticket id, `RunStateBadge`, PR link (only when `prUrl` is non-null), lane outcome from `result`, and a receipt indicator.
3. Add `frontend/src/lib/components/SourcesBanner.svelte`: props `{ sources }`. When every source is missing, it is the whole page content — "no factory run data in this project", naming the paths that were probed, with a line explaining `.factory/` is machine-local and not committed. When some are missing it is a banner above the table naming those, so a null cell is attributable rather than mysterious.
4. Per row, when `unavailable` is non-empty, mark the affected cells as unavailable rather than blank. A blank cell and an unavailable one look identical and mean different things; the row-level `unavailable` list from T81 exists precisely so the view need not guess.
5. Add the route to the existing nav alongside graph/roadmap/search.
6. Check every `RunState` map or switch reachable from this view for exhaustiveness over the widened union, including `RunStateBadge` — if the type allows a state with no branch, that is a build-time error worth having rather than a runtime fallthrough.

## Critical files

- `frontend/src/lib/api/runs.ts` (new)
- `frontend/src/routes/runs/+page.ts` (new)
- `frontend/src/routes/runs/+page.svelte` (new)
- `frontend/src/lib/components/SourcesBanner.svelte` (new)

## Interface & data

`getRuns(): Promise<RunsResponse>`, `getRun(id: string): Promise<RunRecord>` — types generated from the T81 schema, not hand-declared. `SourcesBanner` props `{ sources: Sources }`. Route `/runs`, read-only: no write token, no mutation, no `.factory/` write path anywhere in the client. NFR: loading and error states via the existing `ApiErrorView`; the all-sources-missing case is a first-class rendering, not an error; table is keyed by ticket id.

## Verification

Vitest `runs/page.test.ts` with `$lib/api` mocked. Cases: rows render state, PR link and outcome for a full record; **a response with `sources.*.found === false` renders the no-run-data state and NOT an empty table** — assert the presence of the explanatory content, since a test asserting only "zero rows" passes on the bug this ticket exists to prevent; a partially-available response renders the banner naming exactly the missing sources; a row whose `unavailable` names `results` shows an unavailable marker, distinguishable in the DOM from a blank cell; a `prUrl` of null renders no link element rather than a dead one. `SourcesBanner.test.ts` for the all-missing and some-missing shapes. `RunStateBadge.test.ts` extended to every member of the current union — read the union from the generated types so a future member fails the test rather than passing unnoticed. `pnpm check`, `pnpm test`, `pnpm lint` green.
