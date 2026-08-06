# [T117] Per-request resolution for /tickets, /search, /spend, /runs

milestone: v3.0 · track: backend · depends_on: T111, T22, T23, T90, T98 · provides: the four read-heavy endpoints resolved from the current selection, each offloaded per the house rule, with per-project run-state and `.factory/` artefacts looped by the existing v2.1 rules rather than redesigned.

## Context

The second half of the conversion sweep, split from T116 purely so each PR stays a simple diff over
four files. `tickets.py` carries three handlers (list, detail, deps), all three converted together
because they share one module and one resolution rule.

These are the endpoints where the per-project run-state clarification bites, and **the correct action
is to do nothing new**: each resolved project resolves its OWN run-state source and its own
`.factory/` artefacts by the rules already settled in v2.1 (`.factory/run-state.json` in preference
to a legacy marker directory, no directory form assumed, the four-way resolution, MONOTONICITY). A
registered project whose working copy is not on this machine simply has no source and reports it as
missing — which `/runs` and `/spend` already express through `reason` / `source.found` /
`source.read`. Nothing about that resolution is re-implemented here; the loop over the registry is
future work (the v3.2 dashboard), and per request it is still one project.

`/runs` is already offloaded (T98). `/spend` reads the ledger directly rather than through the
adapter dep, so its conversion touches how it obtains the root but not where its readers live.

## Staged approach

1. EDIT `server/factory_console/api/v1/tickets.py` — all three handlers take
   `root: Path = Depends(get_current_project_root)`; wrap `load_project` and the service call in
   `anyio.to_thread.run_sync(partial(...))` (their first conversion). Update the module docstring.
2. EDIT `server/factory_console/api/v1/search.py` — same swap + offload.
3. EDIT `server/factory_console/api/v1/spend.py` — same swap; offload the ledger read and aggregation
   (it reads and parses `.factory/metrics/ledger.jsonl`, unambiguously blocking).
4. EDIT `server/factory_console/api/v1/runs.py` — swap the root source only; the two `to_thread` hops
   already exist, so this is a two-line change plus a docstring line saying the project is the
   selected one.
5. EDIT the matching integration tests (`test_api_tickets.py`, `test_api_search.py`,
   `test_api_spend.py`, `test_api_runs.py`) — one no-selection 409 case and one unavailable-path 409
   case each. **Add a `/runs` case proving a SELECTED project with no `.factory/` still answers `200`
   with every source `absent`** (not `[]`, not 404) — the honest-missing rule must survive the
   resolution change, and it is the case a multi-project console will hit constantly.
6. Do NOT touch `test_api_runs_concurrency.py`'s subject (the offload) — confirm it still passes.

## Critical files

- `server/factory_console/api/v1/tickets.py` (modify)
- `server/factory_console/api/v1/search.py` (modify)
- `server/factory_console/api/v1/spend.py` (modify)
- `server/factory_console/api/v1/runs.py` (modify)
- `tests/integration/test_api_tickets.py` (modify)
- `tests/integration/test_api_runs.py` (modify)

## Interface & data

**Response shapes: entirely unchanged** — `GET /tickets` → `{items: TicketSummary[], total}`;
`GET /tickets/{id}` → `Ticket`; `GET /tickets/{id}/deps` → `DepNeighborhood`;
`GET /search` → `{items: SearchHit[], total}`; `GET /spend` → `SpendResponse`;
`GET /runs` → `{items: ProjectedRunRecord[], total}`. All per ARCHITECTURE.md → REST v1.
`DISCLOSED_ARTIFACT_FIELDS` and `ProjectedArtifactRead` are untouched — the disclosure boundary does
not move.

New error responses on all four: `409 no_project_selected`, `409 selected_project_unavailable`
(T111's vocabulary).

Contracts by reference: `FileAdapter.list_tickets / get_ticket / get_deps / search_tickets`;
`RunArtifactReader` (T88); the ledger reader; `RunState` resolution and the two write predicates
(ARCHITECTURE.md → "Factory run-state source") — consumed as-is, per project, not redesigned.

DB ops: an indirect registry `SELECT` per request via the resolution seam. NFR flags: anyio offload
added at each touched handler boundary (`/runs` already had it); no auth change; no cache; `/runs`
keeps its deliberate no-pagination decision.

## Verification

`python -m pytest tests/integration/test_api_tickets.py tests/integration/test_api_search.py
tests/integration/test_api_spend.py tests/integration/test_api_runs.py
tests/integration/test_api_runs_concurrency.py -q`, then `python -m pytest -q`. `make lint`.
Manual: boot on `tests/fixtures/projects/factory_layout` and confirm `/api/v1/runs` and
`/api/v1/spend` are byte-identical to pre-change output in pinned mode.
