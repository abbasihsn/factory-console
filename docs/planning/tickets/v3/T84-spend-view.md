# [T84] `/spend` — the cost view

milestone: v3 · track: frontend · depends_on: T82 · provides: a `/spend` route showing total, per-ticket, per-model and per-level factory spend, with the no-ledger case and any partial total stated on the page.

## Context

`GET /api/v1/spend` (T82) returns totals plus three cuts and a `source` block. This renders them, following the same loader/render/test pattern as the other routes.

Real figures from this repository set the design: a single ticket lane cost **$1.40–$5.74**, and one lane's `by_model` mixes three models. So the per-model cut is the interesting one, not an appendix, and the totals are dollars with cents that matter.

Three things this view must state rather than imply.

**No ledger is not zero spend.** `.factory/` is gitignored, so a fresh clone has no ledger, and T82 returns `source.found: false` with zeroed totals. Rendering "$0.00" there is a false claim about real money. The view keys off `source.found`, never off the total being zero.

**A partial total must look partial.** T82 forwards `skipped` lines. If any line was skipped, the totals are low by an unknown amount and the page has to say so next to the number — a footnote elsewhere does not travel with the figure someone screenshots.

**Attributed cost is not additive.** A ledger entry naming several tickets is charged in full to each, so the per-ticket column can sum to more than the total. T82 returns the rule as `attribution`; the view shows it beside the per-ticket table instead of leaving a reader to conclude the arithmetic is broken.

## Staged approach

1. Add `frontend/src/lib/api/spend.ts`: `getSpend()` against the generated types, matching existing api-module conventions.
2. Add `frontend/src/routes/spend/+page.ts` and `+page.svelte`: headline total, then per-ticket, per-model and per-level tables. Reuse `SourcesBanner` (T83) for the missing-source case rather than adding a second banner component.
3. When `source.found === false`, the page renders only the no-ledger explanation — the probed path and a line that `.factory/` is machine-local and not committed. No zeroed tables, because a table of zeros is a claim.
4. When `skipped` is non-empty, render a partial-total marker adjacent to the headline figure, stating how many lines were skipped and that the total excludes them.
5. Render `attribution` next to the per-ticket table, with a one-line explanation of why the column may over-sum.
6. Format currency to cents and token counts with thousands separators; show model ids verbatim as T82 returns them. Do not map an unrecognised model id to a friendly name or to "other" — a model the console has not heard of must be visible as itself.
7. Add the route to the nav beside `/runs`.

## Critical files

- `frontend/src/lib/api/spend.ts` (new)
- `frontend/src/routes/spend/+page.ts` (new)
- `frontend/src/routes/spend/+page.svelte` (new)

## Interface & data

`getSpend(): Promise<SpendResponse>` from the generated T82 schema. Route `/spend`, read-only — no write token, no mutation. Reuses `SourcesBanner` and `ApiErrorView`. NFR: currency to two decimals, rounding only at render (T82 already rounds at its boundary — do not round twice); tables keyed by ticket id / model id / level; no `session_id` is present in the payload to render.

## Verification

Vitest `spend/page.test.ts` with `$lib/api` mocked. Cases: a full response renders the total, all three cuts, and per-model rows for each model id in the fixture; **`source.found === false` renders the no-ledger explanation and NOT a $0.00 total** — assert the absence of a rendered total figure, because a test asserting only "renders without error" passes on the bug; a response with `skipped` non-empty renders the partial marker adjacent to the total, and one with empty `skipped` does not; the `attribution` string is rendered near the per-ticket table; a per-ticket column that over-sums the total does not trigger any error path; an unfamiliar model id renders verbatim rather than being bucketed. `pnpm check`, `pnpm test`, `pnpm lint` green.
