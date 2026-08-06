# [T111] Selection seam: get_current_project_root, and the precedence between a pinned PATH and the persisted selection

milestone: v3.0 · track: backend · depends_on: T106, T107, T20, T98 · provides: one async `get_current_project_root` dependency resolving the SELECTED project per request, the named selection-failure vocabulary shared by every consumer, and the written precedence rule between the boot-time PATH and the persisted selection. No endpoint consumes it yet, so behaviour is unchanged.

## Context

This is the milestone's load-bearing change and it lands FIRST, alone, with no endpoint edits — so it
can be reviewed as a resolution design rather than as a 13-file sweep. Today `create_app` fixes one
`Project` at boot on `app.state.project_root` and all 13 handler sites re-derive from it. v3.0 needs
the *selected* project per request, but the domain/services already take a `Project`, so only the
resolution moves.

**The precedence rule — the thing that was undefined and must not stay undefined.** Two candidate
sources exist: the root discovered from `factory-console PATH`, and the selection persisted in
`console_state` (T105). T119 deliberately ships no `serve` subcommand and no pathless boot, so
*every* successful v3.0 boot has a discovered root. If the pin simply won, the persisted selection
would be permanently shadowed and `PUT /api/v1/projects/current` would change nothing any endpoint
reads — v3.0's headline feature would be inert in the only invocation the milestone ships. If the
persisted selection simply won, `factory-console PATH` would silently serve a different project than
the path the operator typed, contradicting the CLI contract and its own stdout line
`serving <root>`.

**The rule, therefore: the pinned PATH is the SESSION's INITIAL selection.** A process-local
`session_selection` on `app.state` is seeded from `project_root` at boot and OVERWRITTEN in-process
by `PUT /api/v1/projects/current` — which also persists to the registry, so a later boot without a
PATH (v3.1's `serve`) resumes where the operator left off. Both properties hold: the typed path is
what you get, and switching works.

**The selection is persisted, not owned here.** `SelectionState` is a thin read-through over the
registry's `get_selected_project` / `set_selected_project`; the DB row is the durable authority and
its `ON DELETE SET NULL` is what makes "removing the selected project cannot leave a dangling
selection" a schema fact rather than application code. Only the ephemeral session pin — which must
never reach the user's db, or a read-only viewing invocation would mutate it — stays in memory.

`app.state.project_root` SURVIVES as the pinned value and `create_app`'s `project_root` argument
stays required. That is what makes every ticket boundary in this milestone safe: an app built with no
registry is simply an app that is permanently pinned, which is every existing test and today's
behaviour exactly.

## Staged approach

1. CREATE `server/factory_console/services/project_selection.py`:
   - `SESSION_PROJECT_ID: Final = "session"` — the reserved id of the ephemeral, unregistered
     project a `factory-console PATH` boot pins. Document why it is reserved and never persisted.
   - `class SelectionState`: holds `pinned_root: Path | None`, a process-local
     `session_selection: str | None` (seeded to `SESSION_PROJECT_ID` at boot when a pin exists), a
     reference to the `ProjectRegistry`, and a list of `on_change` callbacks. Methods:
     `select(project_id: str | None)` (sets the session selection, persists via
     `registry.set_selected_project`, fires the hooks with the newly resolved root),
     `current_id() -> str | None`, `subscribe(cb: Callable[[Path | None], None])`.
     NOT thread-safe by design and documented as such — single worker, one loop, mutated only from
     handler code. The on-change hook exists here so T114's watcher supervisor can attach WITHOUT
     editing this file again; nothing subscribes yet.
   - Declare the **selection-failure vocabulary ONCE**, as a `Literal` reused by every consumer:
     `SelectionFailure = Literal["no_selection", "selected_project_not_registered",
     "selected_project_missing", "selected_project_unreadable"]`.
   - Error subclasses of `FactoryConsoleError`, in this module because it is where they are raised
     (the `errors.py` convention): `NoProjectSelected` (409 `no_project_selected`),
     `SelectedProjectNotRegistered` (409 `selected_project_not_registered`),
     `SelectedProjectUnavailable` (409 `selected_project_unavailable` — message names the path AND
     which of missing/unreadable it was), `RegistryUnreadable` (503 `registry_unreadable`).
2. EDIT `server/factory_console/api/deps.py`:
   - `get_project_registry(request) -> ProjectRegistry | None` — returns `app.state.project_registry`
     or `None`. `None` is a VALID configuration (pinned mode), so this degrades like
     `get_file_watcher` rather than raising like `get_file_adapter`; state that reasoning in the
     docstring beside the existing three.
   - `get_selection_state(request) -> SelectionState` — raises `RuntimeError` when unbound (a wiring
     bug; `create_app` always sets it).
   - `async def get_current_project_root(request) -> Path` — the seam. Resolution order:
     (a) session selection is `SESSION_PROJECT_ID` and a pinned root exists → the pinned root;
     (b) session selection names a registry id → look it up via
     `await anyio.to_thread.run_sync(partial(registry.get_project, sel))`;
     (c) no session selection and no pin → `NoProjectSelected`;
     (d) id not in the registry → `SelectedProjectNotRegistered`;
     (e) found → `await anyio.to_thread.run_sync(partial(_probe_root, path))`, a stat
     distinguishing present-directory / missing / unreadable, raising `SelectedProjectUnavailable`
     naming which.
     **MONOTONICITY: (d) and (e) NEVER fall back to the pinned root or to another project** — a
     resolution that could not establish its answer refuses.
   - The registry read is `sqlite3`, i.e. blocking I/O, so the dependency itself offloads — the house
     rule is discharged in ONE place for all 13 handler sites instead of 13 times. No caching: a
     fresh connection per request, per T105's design.
3. EDIT `server/factory_console/app.py`: add `project_registry: ProjectRegistry | None = None` to
   `create_app`'s keyword-only params; stash on `app.state.project_registry`; construct
   `app.state.selection = SelectionState(pinned_root=project_root, registry=...)`. Extend the module
   + function docstrings the way every prior port did. **Do NOT change any handler.**
4. CREATE `tests/integration/test_api_selection.py` — cover every branch through a throwaway route
   that `Depends(get_current_project_root)`, using T107's `FakeProjectRegistry`: pinned-only app;
   registry + selection; selection of an unregistered id; selected path deleted; selected path
   chmod-000 (skip on Windows/root); no registry and no pin. Plus the three precedence acceptance
   cases: **(a) boot with PATH + a DB selection pointing elsewhere serves PATH; (b) a subsequent
   `select()` takes effect in the same process; (c) the CLI's stdout line still names the boot-time
   PATH.**
5. EDIT `tests/integration/test_app_factory.py` — assert `app.state.selection` exists and that
   omitting `project_registry` leaves the app pinned.

## Critical files

- `server/factory_console/services/project_selection.py` (create)
- `server/factory_console/api/deps.py` (modify — aggregation file)
- `server/factory_console/app.py` (modify — aggregation file)
- `tests/integration/test_api_selection.py` (create)
- `tests/integration/test_app_factory.py` (modify)

## Interface & data

New DI seam: `async def get_current_project_root(request: Request) -> Path`. Consumers write
`root: Path = Depends(get_current_project_root)`.

**The selection-failure vocabulary, declared here and reused everywhere** —
`no_selection | selected_project_not_registered | selected_project_missing |
selected_project_unreadable`. T112's `reason`, T116's `/health` `selectionReason` and T116–T118's
409 codes all draw from this one union; no consumer invents its own spelling.

The per-endpoint contract this seam establishes (enforced by T116–T118; stated ONCE here):

| Endpoint | No selection | Selected path gone / unreadable |
|---|---|---|
| `/project` `/tickets` `/tickets/{id}` `/tickets/{id}/deps` `/search` `/roadmap` `/graph` `/runs` `/spend` | `409 no_project_selected` | `409 selected_project_unavailable` (names path + which failure) |
| `POST/PATCH/DELETE /tickets*` | same, AFTER the write-token check | same |
| `/health` | `200`, `projectRoot: null` + named `selectionReason` | `200`, `projectRoot` set + `selectionReason` |
| `/projects` `/projects/current` | `200` (they are how you FIX it) | `200`, per-row `condition` |
| `/events` | `200` stream, `ready` + heartbeats only | same |

Never a 404 and never an empty list — a named condition, not a shape that reads as a measurement.

Entities referenced, not redefined: `RegisteredProject` and the `ProjectRegistry` port (T106);
`Project` is still constructed by `FileAdapter.load_project(root)` in the handlers, unchanged.

DB ops: reads the registry through the port; the persisted selection is written via
`set_selected_project`. No migration is owned here. NFR flags: every registry/stat call offloaded
with `anyio.to_thread.run_sync(partial(...))`; no auth change; no cache.

## Verification

`python -m pytest tests/integration/test_api_selection.py tests/integration/test_app_factory.py -q`,
then the full `python -m pytest -q` (85% gate) to prove no existing endpoint changed behaviour.
`make lint`. Manual: `python -m factory_console <fixture>` and confirm `/api/v1/project` and
`/api/v1/health` answer exactly as before.
