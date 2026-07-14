# [T15] Run-state directory prober (run_state.py, read-only)

milestone: MVP · track: file-adapter · depends_on: T07, T08 · provides: `find_run_state_dir(project_root)`; `probe_ticket_state(run_state_dir, ticket_id) -> RunState`

## Context

Factory run-state is authoritative for whether a ticket is mutable (v2 gate) and drives the `RunState` badge in MVP. Probes fallback order (`.factory/run-state/`, `docs/planning/.run-state/`) and normalizes marker files/dirs to `RunState` enum. Explicitly READ-ONLY — no write, mkdir, or unlink. Ever.

## Staged approach

1. `file_adapter/run_state.py`.
2. `find_run_state_dir(project_root: Path) -> Path | None`: probe `[root/'.factory/run-state', root/'docs/planning/.run-state']` in order; return first existing; else `None`.
3. `probe_ticket_state(run_state_dir: Path | None, ticket_id: str) -> RunState`: if `None -> unknown`; re-validate `ticket_id` (defense-in-depth `PathTraversal`); check markers in precedence order `merged > ready > in-flight > todo` (existence as file OR dir wins); if `run_state_dir` exists but no marker -> `todo`.
4. Module-header comment: `# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.`
5. `tests/unit/test_run_state.py`: no dir -> `unknown`; empty dir -> `todo`; each marker as file/dir -> correct enum; both `merged` + `ready` -> `merged` wins; ticket-id traversal refused; fallback order (only `docs/planning/.run-state` present -> used; both present -> `.factory/run-state` wins). Guard test parses module source via `ast/inspect` and asserts no `open(..., 'w'|'a'|'x')`, no `.write_text(`, no `mkdir`, no `unlink`.

## Critical files

- `server/factory_console/file_adapter/run_state.py`
- `tests/unit/test_run_state.py`

## Interface & data

Implements `ARCHITECTURE.md` "Factory run-state directory contract". NFR: read-only invariant asserted by test.

## Verification

`pytest tests/unit/test_run_state.py -q` green including guard test; ruff clean.
