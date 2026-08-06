# [T125] The selected project's degraded conditions, named in the shell

milestone: v3.0 · track: frontend · depends_on: T122, T103, T83 · provides: a shell-level banner that states, by name, when the SELECTED project's path has moved, it is no longer a factory project, it cannot be read, or it has no `.factory/` — so a degraded project never renders as an ordinary empty one.

## Context

The console's whole discipline is that an absent source is a named condition, never zero and never
empty (ARCHITECTURE.md → "Other factory artefacts"). With a registry, new absences become possible
and they are NOT the same thing: a registered path that has moved means every view is about to be
wrong; no `.factory/` means runs/spend/run-state are legitimately missing on this machine (the v3
clarification says so explicitly); an unreadable path means the console could not look at all.

`/projects` (T123) states these per row, but a user who switched and then navigated is looking at
`/runs` or `/graph`, not at `/projects` — so the selected project's condition belongs in the shell,
once, above every route.

## Staged approach

1. CREATE `src/lib/components/ProjectStatusBanner.svelte`, deliberately a SIBLING of `SourcesBanner`
   rather than an extension of it: `SourcesBanner` renders exactly one case (`/runs`' all-absent
   artifacts) and says so in its own header comment, so widening it would blur two different
   subjects. Document the kinship in the new file's comment. Presentational only — no `$app/*`, no
   fetching — props `{ project: RegisteredProjectOut | null }`. It renders one short block per
   degraded condition, each with its own sentence and its own remedy, and renders NOTHING when the
   project is `ok` or when there is no registry (single-project mode is visually unchanged).
   Conditions map through an exhaustive `Record<RegistryEntryCondition, { title, body }>` keyed on the
   generated union, so a condition added server-side is a compile error; an unrecognised value renders
   as itself with a "this console does not recognise this condition" sentence rather than being
   swallowed — **recording what could not be understood, per MONOTONICITY**.
2. `src/routes/+layout.svelte`: render it directly under `<TopBar>`, fed from the layout data's
   selected registry entry, so every route inherits it.
3. CREATE `src/lib/components/ProjectStatusBanner.test.ts`: each of the four degraded conditions
   renders its own distinct text; an `ok` project renders nothing; a null project renders nothing; an
   unknown condition renders itself rather than disappearing.

## Critical files

- `frontend/src/lib/components/ProjectStatusBanner.svelte` (create)
- `frontend/src/routes/+layout.svelte` (modify — aggregation file)
- `frontend/src/lib/components/ProjectStatusBanner.test.ts` (create)

## Interface & data

`ProjectStatusBanner` props `{ project: RegisteredProjectOut | null }`, reading the `condition` field
T112 publishes on a registry entry (consumed by name from the generated types — NOT defined here).

Contracts by reference: ARCHITECTURE.md "Other factory artefacts (read-only)"
(missing-renders-as-missing); "v3 → Run-state + run artefacts, per project" (a project whose working
copy is not on this machine legitimately has no `.factory/`); "The resolution invariant" (an
unrecognised condition is recorded, never dropped); T103's `RegistryEntryCondition`.

DB ops: none. NFR flags: none directly — this is the presentation half of the honest-missing rule.

Aggregation note: `routes/+layout.svelte` is shared with T122 (a dependency of this ticket) and T126,
which depends on this one.

## Verification

`pnpm --dir frontend test`, `pnpm --dir frontend check`, `pnpm --dir frontend lint`, `make lint`.
Manual via `./scripts/dev.sh`: register `tests/fixtures/projects/minimal` (no `.factory/`) and confirm
the banner names it; then `mv` a registered fixture aside and confirm the moved-path condition appears
on every route rather than an empty ticket list.
