# [T81] `GET /api/v1/runs` — what the factory did, per ticket

milestone: v3 · track: backend · depends_on: T78 · provides: a read-only runs endpoint returning per-ticket run status, PR url, lane result and receipt presence, with every absent source named rather than rendered as an empty success.

## Context

After T78 the console can read `.factory/run-state.json`. The factory leaves more beside it: `.factory/results/T*.json` (per-ticket lane result), `.factory/receipts/T*.json` (review receipts), `.factory/reports/sprint-*.json`, `.factory/last-stop.json` (why the last run stopped). None of it is reachable from the console.

This endpoint composes them into one record per ticket. It is read-only — the console never writes under `.factory/`, and this ticket does not change that.

The hard part is not composition, it is absence. `.factory/` is gitignored, so in a fresh clone **all** of these are missing, and the endpoint must be able to say "there is no run data here" as a fact rather than returning 77 tickets with every field null — which reads as "the factory ran and did nothing". Each source is independently optional: a project can have a run-state and no receipts, or results and no ledger.

The result and receipt schemas belong to the factory, not the console. This ticket reads a **named subset** of fields and treats the rest as opaque. A previous session in this program queried a lane result for fields that belonged to a different schema, got `null` for each, and began writing up a violation — **a `null` from a query is a fact about the query until the schema has been checked.** So: the fields read here must each be shown to exist in a real file in this repository, and any field not found is reported as unavailable, never as absent-therefore-false.

## Staged approach

1. Add `server/factory_console/domain/run_record.py`: frozen `RunRecord` with `ticketId`, `runState: RunState`, `prUrl: str | None`, `result: RunResultSummary | None`, `hasReceipt: bool`, and `unavailable: list[str]` naming every source that could not be read for this ticket. The `unavailable` list is the ticket's contract with the UI: a field is null *because* something named here was missing.
2. Add `server/factory_console/file_adapter/runs.py`: `find_results_dir`, `find_receipts_dir`, `read_result(project, ticket_id) -> RunResultSummary | None`, `read_last_stop(project) -> LastStop | None`. Validate the ticket id and reject traversal before joining any path, reusing `path_safety` — the id reaches these functions from a URL.
3. `RunResultSummary` reads only fields verified present in a real `.factory/results/T*.json` in this repository. Read the file, list its keys, and model those. A field the file does not have is not modelled and not guessed. Extra fields are ignored (`extra="ignore"`) for the same reason as T79: the factory owns this schema and may extend it.
4. Add `api/v1/runs.py` with `GET /api/v1/runs` (all tickets) and `GET /api/v1/runs/{ticket_id}` (one), and register it in `api/v1/__init__.py` with one `include_router` line, matching the seam the existing routers use.
5. The list response carries a top-level `sources` object naming, for each of run-state / results / receipts / last-stop, whether it was found and at which path. This is how a caller tells "no run data" from "runs with nothing in them" — the same distinction T79 draws for the ledger, at the API boundary.
6. Do not surface `session_id` or absolute machine paths outside the project root in any response.

## Critical files

- `server/factory_console/domain/run_record.py` (new)
- `server/factory_console/file_adapter/runs.py` (new)
- `server/factory_console/api/v1/runs.py` (new)
- `server/factory_console/api/v1/__init__.py`

## Interface & data

`GET /api/v1/runs` → `{"sources": {"runState": SourceInfo, "results": SourceInfo, "receipts": SourceInfo, "lastStop": SourceInfo}, "runs": [RunRecord], "lastStop": LastStop | null}`; `SourceInfo = {"found": bool, "path": str | null}` with `path` project-relative. `GET /api/v1/runs/{ticket_id}` → `RunRecord`, 404 for an unknown ticket id (unknown to the *manifest* — distinct from a ticket the run-state does not list, which is a `RunRecord` with `runState: "absent"` after T80). `RunRecord(ticketId, runState, prUrl, result, hasReceipt, unavailable: list[str])`. Errors use the existing `ApiError` envelope and `error_handlers`. On-disk contracts consumed read-only: `.factory/run-state.json`, `.factory/results/T*.json`, `.factory/receipts/T*.json`, `.factory/last-stop.json`. NFR: read-only; ticket ids validated + traversal-checked before any join; no session ids or out-of-root paths in responses; response bounded by the manifest's ticket count.

## Verification

Pytest `test_api_runs.py` against a fixture project built from **real files copied out of this repository's `.factory/`**, not hand-written stand-ins. Cases: a ticket with run-state + result + receipt returns all three and an empty `unavailable`; a ticket with run-state only returns nulls **and names `results` and `receipts` in `unavailable`** — assert the naming, since a response that merely nulls the fields is the failure this ticket exists to prevent; a project with no `.factory/` at all returns `sources.*.found == false` for every source and is distinguishable from a project whose sources exist and are empty; `GET /runs/{id}` with `../` or a non-matching id is rejected before any filesystem access; an unknown-to-manifest id is 404 while a known id absent from run-state is 200 with `runState: "absent"`. Assert each modelled `RunResultSummary` field against the real file it was derived from — a field asserted only against a fixture the ticket also wrote proves nothing about the factory. `make lint`, `pytest` green.
