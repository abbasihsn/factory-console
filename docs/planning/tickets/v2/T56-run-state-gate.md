# [T56] RunStateGate — enforce todo-only mutability, reusing the run-state prober

milestone: v2 · track: file-adapter · depends_on: T04, T15 · provides: file_adapter/write_gate.py — ensure_mutable(project, ticket_id) allowing only todo/unknown; TicketNotMutable (409) for in-flight/ready/merged.

## Context

The core v2 safety invariant: a ticket is editable ONLY when its factory RunState is `todo` (or `unknown` when no run-state dir exists); `in_flight`/`ready`/`merged` are read-only, matching how `/factory-reconcile-plan` treats them. This gate is the single chokepoint every mutating write passes before touching disk. It REUSES the existing read-only run-state prober — never re-implements run-state detection and never writes to the run-state directory. This `TicketNotMutable` / `ticket_not_mutable` is the ONE canonical error for the non-todo condition across the whole write path (WriteService does not define a second one).

## Staged approach

1. CREATE `server/factory_console/file_adapter/write_gate.py`.
2. Define `TicketNotMutable(FactoryConsoleError)`: `code='ticket_not_mutable'`, `status=409`, message naming the state, `details={'ticketId', 'runState'}`.
3. Define `MUTABLE_STATES = (RunState.todo, RunState.unknown)` — the ONLY editable predicate (ARCHITECTURE "Factory run-state directory").
4. Define `ensure_mutable(project: Project, ticket_id: str) -> RunState`: call `probe_ticket_state(project.runStateDir, ticket_id)` from `file_adapter.run_state` (reuse, do not reimplement); if the resolved state is not in `MUTABLE_STATES`, raise `TicketNotMutable(ticket_id, run_state)`; otherwise return the state. A `PathTraversal` from the prober (unsafe id) propagates unchanged.
5. Import the gate by full path elsewhere; do NOT edit `file_adapter/__init__.py`.

## Critical files

- `server/factory_console/file_adapter/write_gate.py` (new)
- `tests/unit/test_write_gate.py` (new)

## Interface & data

`ensure_mutable(project: Project, ticket_id: str) -> RunState` — returns the RunState on success; raises `TicketNotMutable` (409) otherwise. Contracts by reference: reuses `probe_ticket_state` + `RunState` enum + the run-state directory contract (ARCHITECTURE "Factory run-state directory (read-only)") and `PathTraversal` (invalid_ticket_id) semantics. Error envelope per REST v1 `{error:{code,message,details}}`. No DB. NFR: this IS the write safety/authorization invariant; read-only probe only — MUST NOT write to `project.runStateDir`.

## Verification

`pytest tests/unit/test_write_gate.py` against a `Project` pointed at `tests/fixtures/projects/with_run_state`: `ensure_mutable` returns for todo/unknown without raising and raises `TicketNotMutable` for CAD-125 (in-flight) / CAD-118 (ready) / CAD-100 (merged). Property-style: for every `RunState` member, `ensure_mutable` raises iff the state is in-flight/ready/merged. Assert no filesystem mutation occurs.
