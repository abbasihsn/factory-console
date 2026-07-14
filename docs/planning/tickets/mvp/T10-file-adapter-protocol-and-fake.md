# [T10] FileAdapter Protocol + FakeFileAdapter (in-memory)

milestone: MVP · track: file-adapter · depends_on: T07 · provides: `FileAdapter` port + `FakeFileAdapter` — unblocks backend to code against the seam before `RealFileAdapter` exists

## Context

The `FileAdapter` Protocol is the internal seam between HTTP handlers and filesystem I/O. Ship it (and an in-memory `FakeFileAdapter`) before any real parser so backend can wire endpoints against the port immediately.

## Staged approach

1. `file_adapter/__init__.py` re-exports `FileAdapter` + `FakeFileAdapter`.
2. `file_adapter/protocol.py`: `@runtime_checkable class FileAdapter(Protocol)` with `load_project(root: Path) -> Project`; `list_tickets(project: Project) -> list[TicketSummary]`; `get_ticket(project: Project, ticket_id: str) -> Ticket | None`; `get_deps(project: Project, ticket_id: str) -> DepNeighborhood | None`; `read_run_state(project: Project, ticket_id: str) -> RunState`; `get_roadmap(project: Project) -> Roadmap | None`.
3. `file_adapter/fake.py`: `FakeFileAdapter(project: Project, tickets: list[Ticket], run_states: dict[str, RunState] | None, roadmap: Roadmap | None)`. Methods return in-memory data; `list_tickets` projects to `TicketSummary` + computes `depCount/dependentCount` by reverse-indexing `dependsOn`; `get_deps` reverse-indexes dependents + marks `unresolvedDeps`.
4. `tests/unit/test_fake_file_adapter.py`: `isinstance(fake, FileAdapter)` runtime check passes; all six methods return expected shapes; unresolved deps land in `unresolvedDeps`; unknown id returns None.

## Critical files

- `server/factory_console/file_adapter/__init__.py`
- `server/factory_console/file_adapter/protocol.py`
- `server/factory_console/file_adapter/fake.py`
- `tests/unit/test_fake_file_adapter.py`

## Interface & data

Implements `ARCHITECTURE.md` "FileAdapter port" contract. Consumes T07 domain models. No I/O. NFR: read-only, side-effect-free.

## Verification

`pytest tests/unit/test_fake_file_adapter.py -q` green; ruff clean.
