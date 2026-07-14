# [T07] Domain models (Project, Ticket, TicketSummary, RunState, DepNeighborhood, Roadmap) + TICKET_ID_PATTERN

milestone: MVP · track: file-adapter · depends_on: T02 · provides: Shared Pydantic v2 domain models used by file-adapter, backend, and (transitively via OpenAPI) frontend generated types; module constant `TICKET_ID_PATTERN`

## Context

Every other track depends on these models. Ship them first so all downstream tickets can compile against a stable type surface. Models mirror `ARCHITECTURE.md` data_model exactly. `TICKET_ID_PATTERN` is the SINGLE source of truth for ticket-id validation (path-traversal defense); file-adapter modules reuse it defense-in-depth, backend `Path()` params import it verbatim.

## Staged approach

1. `server/factory_console/domain/__init__.py` re-exports public names + `TICKET_ID_PATTERN`.
2. `domain/run_state.py`: `class RunState(str, Enum) = {todo, in_flight, ready, merged, unknown}`.
3. `domain/project.py`: `Project(rootPath: Path, ticketsManifestPath: Path, ticketsDir: Path, roadmapPath: Path | None, runStateDir: Path | None, discoveredAt: datetime)`.
4. `domain/ticket.py`: `TICKET_ID_PATTERN = r'^[A-Za-z0-9_.-]+$'`; `TicketId = Annotated[str, StringConstraints(pattern=TICKET_ID_PATTERN)]`; `Ticket(id: TicketId, title, status, track|None, milestone|None, dependsOn: list[str], provides: list[str], files: list[str], filePath, bodyMarkdown, bodyHtml, raw: dict)`; `TicketSummary(id: TicketId, title, status, track, milestone, runState: RunState, depCount, dependentCount)`.
5. `domain/deps.py`: `DepNeighborhood(ticket: TicketSummary, directDeps: list[TicketSummary], directDependents: list[TicketSummary], unresolvedDeps: list[str])`; `Roadmap(path, bodyMarkdown, bodyHtml)`.
6. `model_config = ConfigDict(frozen=True, extra='forbid')` except `Ticket.raw` passes through.
7. `tests/unit/test_domain_models.py`: valid id accepted; `'/'`, `'.'`, space, empty rejected; `model_dump` round-trip; `RunState` enum values stable.

## Critical files

- `server/factory_console/domain/__init__.py`
- `server/factory_console/domain/run_state.py`
- `server/factory_console/domain/project.py`
- `server/factory_console/domain/ticket.py`
- `server/factory_console/domain/deps.py`
- `tests/unit/test_domain_models.py`

## Interface & data

Implements `ARCHITECTURE.md` data_model. `TICKET_ID_PATTERN = ^[A-Za-z0-9_.-]+$`. No I/O. NFR: input-validation (path-traversal defense at type layer); frozen models.

## Verification

`pytest tests/unit/test_domain_models.py -q` green; `python -c 'from factory_console.domain import Project, Ticket, TicketSummary, RunState, DepNeighborhood, Roadmap, TICKET_ID_PATTERN'` succeeds; ruff clean.
