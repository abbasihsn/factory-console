# [T124] Add a project by path, and render the server's named refusal

milestone: v3.0 · track: frontend · depends_on: T123, T113 · provides: the add-project form on `/projects` — register a path with an optional name — whose failure state is the server's NAMED validation error, never a generic "something went wrong".

## Context

Registering a project is the only way the registry ever gets populated, and it is the one place a
user hands the console a path that may be wrong in several distinct ways (does not exist, is not a
directory, carries no `docs/planning/tickets.json`, is already registered). The store track owns
those rules; **this ticket's whole discipline is to SHOW which one fired**, using the error envelope
the server already sends, rather than collapsing them into one message the console invented and would
drift from.

A second copy of the store's path rules living in the browser is exactly the
two-answers-to-one-question defect class v2.2 was filed against — and the browser cannot see the
filesystem anyway, so any client-side guess would be fiction.

## Staged approach

1. CREATE `src/lib/components/AddProjectForm.svelte`: a labelled path input (required, monospace), an
   optional name input, and a Register button. **Client-side validation is limited to "the path box
   is empty"** — every other rule is the server's. On submit it calls
   `addProject({ path, name }, token)`, clears the fields on success and invokes an `onAdded`
   callback; on `ApiError` it renders `ApiErrorView` with `compact` and `actionLabel="Try again"`,
   which prints the server's `code`, `message` and any `hint` verbatim through `normalizeError`; a
   `write_token_invalid` 401 raises `WriteTokenPrompt` and retries, as elsewhere. Busy state disables
   the submit so a slow filesystem check cannot be double-submitted.
2. `src/routes/projects/+page.svelte`: mount the form above the table, wire `onAdded` to
   `invalidateAll()` so the new row and the new switcher entry both appear from a re-read, and point
   the empty-registry panel at it.
3. CREATE `src/lib/components/AddProjectForm.test.ts`: submit calls `addProject` with the trimmed path
   and the name; the empty-path guard keeps submit inert; **a named server refusal renders its `code`
   AND its `message`** (assert both, so a regression to a generic message fails); the 401 raises the
   prompt; success clears the fields and calls `onAdded`.

## Critical files

- `frontend/src/lib/components/AddProjectForm.svelte` (create)
- `frontend/src/routes/projects/+page.svelte` (modify — aggregation file)
- `frontend/src/lib/components/AddProjectForm.test.ts` (create)

## Interface & data

`AddProjectForm` props `{ onAdded?: () => void }`; calls
`addProject({ path: string; name?: string }, token: string): Promise<RegisteredProjectOut>`.

Contracts by reference: the REST v1 add endpoint (T113) and the shared error envelope
`{ error: { code, message, details? } }` rendered through `normalizeError` + `ApiErrorView`; the
write-token scheme; `RegisteredProject`. The distinct codes this form must be able to surface:
`invalid_project_path`, `project_not_found`, `malformed_manifest`, `duplicate_project_path`.

DB ops: none client-side — **path validation is entirely server-side**. NFR flags: auth = write
token; no client-side path parsing or filesystem assumptions; submit disabled while in flight.

Aggregation note: `routes/projects/+page.svelte` is shared with T123, which this ticket depends on.

## Verification

`pnpm --dir frontend test`, `pnpm --dir frontend check`, `pnpm --dir frontend lint`, `make lint`.
Manual via `./scripts/dev.sh`: register a real fixture (`tests/fixtures/projects/minimal`) and see it
appear in both the table and the switcher; then submit a nonexistent path, a file, and a directory
with no manifest, and confirm **three DIFFERENT named messages** come back.
