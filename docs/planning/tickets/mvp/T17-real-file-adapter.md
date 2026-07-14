# [T17] RealFileAdapter composing discovery + manifest + ticket_md + markdown + run_state

milestone: MVP · track: file-adapter · depends_on: T10, T11, T12, T13, T14, T15 · provides: `RealFileAdapter` implementing all six `FileAdapter` methods — the production implementation the CLI wires up

## Context

Composes all small modules into the six-method Protocol. Every request re-reads files (no cache) per `ARCHITECTURE.md`; adapter is stateless beyond the `Project` handed to it. Validated by an integration test against `tests/fixtures/projects/with_run_state/`.

## Staged approach

1. `file_adapter/real.py`.
2. `class RealFileAdapter`:
   - `load_project(root)` — discover manifest (else `ProjectNotFound`), compute `ticketsDir`, `roadmapPath` (if `ROADMAP.md` at root or `docs/`), `runStateDir` (via `find_run_state_dir`), stamp `discoveredAt`.
   - `list_tickets(project)` — load manifest stubs, build reverse-index for dependents, probe `run_state` per ticket, project to `TicketSummary`.
   - `get_ticket(project, id)` — load manifest, find entry (`None` if absent), enrich via `ticket_md`, render `bodyHtml`.
   - `get_deps(project, id)` — load summaries, build `directDeps` + `directDependents`, compute `unresolvedDeps`.
   - `read_run_state(project, id)` — delegate to `probe_ticket_state`.
   - `get_roadmap(project)` — read file if `roadmapPath`, render `bodyHtml`, else `None`.
3. Ensure `isinstance(RealFileAdapter(), FileAdapter)` runtime.
4. `tests/integration/test_real_file_adapter.py` against `tests/fixtures/projects/with_run_state/`: `load_project` returns fully-populated `Project`; `list_tickets` returns expected count + per-ticket `runState` + dep/dependent counts; `get_ticket('known')` returns full `Ticket` with rendered `bodyHtml`; `get_ticket('missing')` returns `None`; `get_deps('known')` returns `DepNeighborhood` with correct directs + reverse + unresolved; `read_run_state('in-flight-id') == RunState.in_flight`; `get_roadmap` returns `Roadmap`. Against `malformed/`: `list_tickets` raises `MalformedManifest`.

## Critical files

- `server/factory_console/file_adapter/real.py`
- `tests/integration/test_real_file_adapter.py`

## Interface & data

Implements all six `FileAdapter` signatures. Composes T10-T15 modules. NFR: read-only, stateless per instance.

## Verification

`pytest tests/integration/test_real_file_adapter.py -q` green; `grep -R "open(" server/factory_console/file_adapter/` returns only read-mode calls; ruff clean.
