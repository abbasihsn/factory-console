# [T123] /projects route — list registered projects, select and remove

milestone: v3.0 · track: frontend · depends_on: T121, T122, T112, T103, T69 · provides: a `/projects` route listing every registered project with its named condition, selecting one, and removing one behind the existing ConfirmDialog — plus its nav entry.

## Context

The switcher can choose among registered projects but nothing can manage the registry itself. This
route is the management surface, and it is where a registered project's health is stated plainly: a
path that has moved, a project that is no longer a factory project, one whose path cannot be read,
one with no `.factory/`. Removal is destructive to console state only (never to the project's files),
which is exactly what the confirmation must say.

Add is a separate, following ticket (T124) so this one stays a single concern.

## Staged approach

1. CREATE `src/routes/projects/+page.ts`: `load` calls `listProjects()` and returns `{ projects }`;
   failures go to `throwBoundaryError` from `$lib/api/loadError` (imported directly, not via the
   barrel, so the load test can mock the barrel — the pattern every other loader uses).
2. CREATE `src/routes/projects/+page.svelte`: an `<h1>Projects</h1>` and a table — name, path
   (`font-mono`, `title` for the full value), added-at, condition, actions. The selected row is marked
   (`aria-current`) and its Select button is inert.
   **Condition** renders from an exhaustive `Record<RegistryEntryCondition, string>` label + title map
   keyed on the generated union (the discipline `RunStateBadge` and `/runs`' `REASON_LABELS` set), so
   a condition added server-side and regenerated is a compile error rather than a blank cell; an
   unrecognised value renders as itself with a "this console does not recognise this condition" title
   rather than being dropped.
   Select reuses the same `selectProject` + `invalidateAll()` path as the switcher. Remove opens the
   existing `ConfirmDialog` whose body states that removal forgets the project in the console registry
   and touches nothing on disk, then calls `removeProject` and `invalidateAll()`. Errors from either
   mutation render through `ApiErrorView` with `compact`, fed by the server's own normalized envelope;
   a `write_token_invalid` 401 raises `WriteTokenPrompt` as elsewhere.
3. An empty registry is a NAMED state, not a blank table: a short panel saying no project is
   registered yet and pointing at the add form (which T124 fills in).
4. `src/lib/components/NavSearch.svelte`: add the `Projects` header link so the route is
   discoverable; update `NavSearch.test.ts`'s link assertions.
5. `src/lib/components/ProjectSwitcher.svelte`: add a trailing "Manage projects…" entry linking to
   `/projects` (safe now the route exists).
6. CREATE `src/routes/projects/page.test.ts`: load happy path + boundary error; rendering of the
   list, the selected marker, each of the five condition labels, and the empty-registry panel; remove
   behind the confirm dialog; the 401 path.

## Critical files

- `frontend/src/routes/projects/+page.ts` (create)
- `frontend/src/routes/projects/+page.svelte` (create — aggregation file)
- `frontend/src/lib/components/NavSearch.svelte` (modify)
- `frontend/src/lib/components/ProjectSwitcher.svelte` (modify — aggregation file)
- `frontend/src/routes/projects/page.test.ts` (create)
- `frontend/src/lib/components/NavSearch.test.ts` (modify)

## Interface & data

`PageData = { projects: RegisteredProjectOut[] }`; actions call `selectProject(id, token)` and
`removeProject(id, token)` from `$lib/api`.

Contracts by reference: REST v1 registry endpoints (T112/T113); `RegisteredProject
{ id, name, path, addedAt }` plus the per-row `condition` field (consumed by name from the generated
types, never redefined here); T103's `RegistryEntryCondition`; the error envelope via
`normalizeError`.

DB ops: none client-side. NFR flags: auth = write token on select/remove; the destructive action is
gated by `ConfirmDialog`; **no optimistic UI** — the list is re-read via `invalidateAll()` so the
server stays the single source of truth.

Aggregation note: `ProjectSwitcher.svelte` is shared with T122, which this ticket depends on, so the
two serialize.

## Verification

`pnpm --dir frontend test`, `pnpm --dir frontend check`, `pnpm --dir frontend lint`, `make lint`.
Manual via `./scripts/dev.sh`: `/projects` lists the registry, Select re-points every view, Remove
asks first and then drops the row and the switcher entry.
