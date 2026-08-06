# [T141] /runs — one PR answer, with its source named

milestone: v3.0.1 · track: frontend · depends_on: T140, T83, T100, T102 · provides: the existing `/runs` PR column becomes ONE answer with a declared precedence — the GitHub source when it carries a pull request, the artifact `pr_url` labelled as such otherwise — instead of two competing PR claims in one view.

## Context

`/runs` already renders a PR column, read from `pr_url` in the factory's own lane-result artifact via
the enumerated `PROJECTED_FIELDS`, guarded by `projected-fields.test.ts`. v3.0.1 adds a second,
better-founded PR source.

**Adding a second COLUMN would put two answers to one question side by side in one view** — the exact
defect class v2.2 was filed against (T102: "two endpoints take opposite positions"; T93: "the same
fact answered differently in two places"). So the column stays one column and gains a stated
precedence, and the cell says which source it came from.

**The precedence, exactly:**
1. the GitHub entry carries a `pullRequest` → render it (a live, verified link);
2. **any other entry state — including `no_pull_request`** → fall back to the artifact `pr_url`,
   labelled as artifact-sourced;
3. neither → the existing named reason cell.

Step 2 is the subtle one and is deliberate: `no_pull_request` means "no PR under any branch name this
console recognises", which rests on the unverified `tkt/<id>` convention (T136). It is the console's
ignorance, not a fact about GitHub, so it must not erase an artifact value a lane actually wrote.

`PROJECTED_FIELDS` and `projected-fields.test.ts` are **explicitly NOT touched**: the artifact reading
path is unchanged, it simply stops being the only answer.

## Staged approach

1. `src/routes/runs/+page.ts`: keep the existing `listTickets` + `getRuns` loads exactly as they are.
   **Do not add the PR fetch to the load** — it is client-side and outside the invalidation path, for
   the reason T140 establishes. Wire the same client-side PR store this route can read.
2. `src/routes/runs/+page.svelte`: keep the `PR` column and give it ONE documented precedence, written
   as a comment at the top of the cell:
   - GitHub carries a `pullRequest` → render `PrStatusBadge`; cell carries `data-pr-source="github"`;
   - otherwise → the existing artifact `prLink()` path unchanged; cell carries
     `data-pr-source="artifact"` and a title naming the artifact as its source;
   - neither → the existing named `reasonCell`.
   The two sources therefore never appear at once, and a reader can always tell which one spoke.
   While the PR fetch is pending, render the artifact value (it is already loaded) rather than a
   spinner — the page must not get slower than it is today.
   **Do NOT touch** the `PROJECTED_FIELDS` module block, `readString`, `readField`, `prLink`, or the
   Outcome/Receipt columns.
3. When the GitHub source is unavailable PROJECT-WIDE (no `gh`, wrong account, not a git repo, no
   remote), render one line above the table naming that condition once — in `SourcesBanner`'s idiom,
   so the fallback is disclosed rather than silent. **Absence of GitHub must never render as "this
   ticket has no PR".**
4. `src/routes/runs/page.test.ts`: precedence in all three branches; `data-pr-source` on both value
   branches; the `no_pull_request` → artifact fallback specifically (the correction this ticket
   exists to encode); the project-wide unavailable line; the table still renders when the PR fetch
   failed. **Confirm `projected-fields.test.ts` passes UNCHANGED — do not edit it.**

## Critical files

- `frontend/src/routes/runs/+page.ts` (modify)
- `frontend/src/routes/runs/+page.svelte` (modify)
- `frontend/src/routes/runs/page.test.ts` (modify)

## Interface & data

`RunRow` gains the ticket's PR entry and the project-level PR source block; no change to `ticketId` /
`title` / `runState` / `record`.

Contracts by reference: REST v1 `GET /api/v1/runs` (`ProjectedRunRecord`, `ProjectedArtifactRead`,
`DISCLOSED_ARTIFACT_FIELDS`) and T139's PR endpoint; the disclosure rule and `PROJECTED_FIELDS`, both
of which this ticket consumes and **neither of which it changes**; T131's precedence paragraph.

DB ops: none. NFR flags: a GitHub failure is non-fatal to the page; the untrusted-URL guard applies to
both sources; no caching; no additional `gh` call on SSE invalidation.

**EXPLICIT non-goals:** no second PR column; no new entry in `PROJECTED_FIELDS`; no edit to
`projected-fields.test.ts`.

## Verification

`pnpm --dir frontend test` (including the untouched `projected-fields.test.ts`),
`pnpm --dir frontend check`, `pnpm --dir frontend lint`, `make lint`.
Manual via `./scripts/dev.sh` on a project with a GitHub remote and on
`tests/fixtures/projects/with_run_state` (artifact-only), confirming the same column answers from a
different, NAMED source in each — and that a ticket with no matching `tkt/` branch still shows its
artifact PR link rather than losing it.
