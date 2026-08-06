# [T110] resolve_entries — the length-preserving registry/condition join

milestone: v3.0 · track: store · depends_on: T109, T107 · provides: `store/entries.py::resolve_entries()` — the pure fold that turns registry rows into the `RegistryEntry` list the backend serialises and the SPA renders from its exhaustive label map, with the invariant that a degraded row is transformed and NEVER filtered.

## Context

T109 can say what a single path currently is; the registry can list rows. Joining them is one small
pure function, and it gets its own module (and its own ticket) because the join carries an invariant
worth stating and testing on its own: **`len(result) == len(projects)`, always**.

That is not pedantry. The whole point of the condition vocabulary is that a registered project whose
path has gone away must still appear — a row silently dropped from the listing reads to the user as
"I never registered that", which is a false statement the console would be making about the user's
own action. Filtering is the tempting implementation (a shorter, cleaner list) and it is precisely
the failure ARCHITECTURE.md's resolution invariant names: discarding what could not be established
"leaves a collection that looks smaller and cleaner, which reads downstream as *more* information,
not less."

Keeping the fold out of the handler means the ordering rule and the never-drop rule are one testable
function rather than a loop inside an endpoint, matching how `RunService` composes for `/runs`.

## Staged approach

1. CREATE `server/factory_console/store/entries.py`.
2. Define `resolve_entries(projects: Iterable[RegisteredProject], probe: ProjectConditionProbe) ->
   list[RegistryEntry]` — one `probe` call per row, preserving input order, doing no I/O of its own
   (the probe owns all of it).
3. Module docstring: why the join lives here and not in a handler; and state the invariant
   explicitly — `len(result) == len(projects)`, ALWAYS; a degraded row is transformed, never
   filtered; cite ARCHITECTURE.md's "The resolution invariant" for the recorded-never-dropped rule.
4. Note that the function is synchronous and the caller offloads it, because the injected probe does
   blocking `stat` work — the backend performs the registry query and this fold in a single
   `anyio.to_thread.run_sync` hop rather than one hop per row (T112).
5. Do NOT touch `store/__init__.py`; import by full path.
6. CREATE `tests/unit/test_store_entries.py`: order preservation; a mixed list of five rows yields
   five entries with the five distinct conditions; an empty input yields `[]`; the probe is called
   exactly once per row; **a degraded row is never dropped** (the regression test for the invariant).

## Critical files

- `server/factory_console/store/entries.py` (create)
- `tests/unit/test_store_entries.py` (create)

## Interface & data

`resolve_entries(projects: Iterable[RegisteredProject], probe: ProjectConditionProbe) ->
list[RegistryEntry]` — length-preserving, order-preserving, no I/O of its own.

Referenced, not redefined: `domain/registry.py::RegisteredProject` / `RegistryEntry` /
`RegistryEntryCondition`, `file_adapter/project_condition.py::ProjectConditionProbe`,
ARCHITECTURE.md "The resolution invariant".

DB ops: none. NFR flags: pure composition (the `services/`-layer rule from PROJECT_STRUCTURE's Track
ownership, applied to a store-side fold); synchronous — the caller offloads with anyio, one hop for
the whole listing rather than one per row.

## Verification

`python -m pytest tests/unit/test_store_entries.py -q`;
`python -m pytest -q --cov=factory_console`; `make lint`. Nothing is wired yet, so
`factory-console PATH` is unaffected.
