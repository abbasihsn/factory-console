# [T36] Full-text search capability behind the FileAdapter port

milestone: v1 · track: file-adapter · depends_on: T07, T10, T12, T13, T17 · provides: FileAdapter.search_tickets + SearchHit model + pure file_adapter/search.py ranking over ticket id/title/provides/body

## Context

v1 adds a global search box; the SPA needs to find tickets by words in their id, title, `provides`, or rendered body — not just the MVP list route's id/title substring filter (`GET /api/v1/tickets?q=`). Because ticket bodies live on disk and the `FileAdapter` is the ONLY filesystem seam (ARCHITECTURE "## Contracts / FileAdapter port"), the body-reading scan MUST live in the adapter; the backend endpoint (T41) then only shapes/paginates. This ticket delivers the ranked hit list that `GET /api/v1/search` will return. It is distinct from — not a duplicate of — the T22 list filter.

## Staged approach

1. Add `server/factory_console/domain/search.py`: a frozen Pydantic `SearchHit` model `{ ticket: TicketSummary, score: float, matchedFields: list[str] }` (`extra='forbid'`, camelCase). Import it by full path from consumers; do NOT add it to `domain/__init__.py` (keeps that aggregation file collision-free across the parallel v1 tickets).
2. Add `server/factory_console/file_adapter/search.py`: a pure `rank_tickets(tickets: list[Ticket], query: str) -> list[ScoredTicket]` (`ScoredTicket` = internal dataclass id/score/matched_fields). Lowercase the query, split on whitespace into tokens; per token, add a per-field weight when the token is a substring of that field (weights: id = title > provides > bodyMarkdown); `matchedFields` = the set of fields any token hit; drop score==0 tickets; sort by score desc, stable on input order; blank/whitespace query returns `[]`. No external dependency.
3. Add `search_tickets` to the `FileAdapter` Protocol in `file_adapter/protocol.py` (shared with T37 — listed in critical_files so the overlap filter serializes them).
4. Implement in `real.py`: materialize manifest stubs via `iter_ticket_stubs`, build the shared `TicketProjection` (reusing `_safe_run_state` so a bad id degrades, not crashes), enrich EACH stub with its body TOLERANTLY (`read_ticket_md`; on `TicketFileMissing`/`TicketFileUnreadable`/`PathTraversal` fall back to an empty body so one bad `.md` never fails the whole scan), call `rank_tickets`, then map each `ScoredTicket` to `SearchHit(ticket=summary_by_id[id], score, matchedFields)` from `projection.summaries()`; apply `limit`.
5. Implement in `fake.py`: store the seeded tickets on `self._tickets` in `__init__`, then `search_tickets` calls `rank_tickets(self._tickets, query)` and maps to `SearchHit` via the projection.
6. Note in-code: re-read per request, no cache/index — consistent with ARCHITECTURE's "every request re-reads" (an in-memory index is deferred to a later milestone alongside the watcher).

## Critical files

- `server/factory_console/domain/search.py` (new — SearchHit)
- `server/factory_console/file_adapter/search.py` (new — pure rank_tickets)
- `server/factory_console/file_adapter/protocol.py` (add search_tickets to the Protocol)
- `server/factory_console/file_adapter/real.py` (implement — tolerant body enrichment)
- `server/factory_console/file_adapter/fake.py` (implement — in-memory)

## Interface & data

- `FileAdapter.search_tickets(project: Project, query: str, *, limit: int | None = None) -> list[SearchHit]`; pure `rank_tickets(tickets, query) -> list[ScoredTicket]`.
- Touched BY REFERENCE (do not redefine): the `FileAdapter` Protocol (adds one method), `Ticket`/`TicketSummary`/`TICKET_ID_PATTERN` and the run-state degradation from `domain.ticket` + `real.py`'s `_safe_run_state`, and the shared `TicketProjection.summaries()`/`ticket_for()`.
- New model `SearchHit { ticket: TicketSummary, score: float, matchedFields: list[str] }` (frozen, `extra='forbid'`, camelCase). See ARCHITECTURE "## Data model" for `TicketSummary`.
- DB ops: N/A (files are the source of truth). NFR: no cache / re-read per request (O(n) FS scan reading every ticket `.md`); read-only; tolerant enrichment (missing/unreadable body degrades to empty, never 500s the scan).

## Verification

`pytest` unit tests for `rank_tickets` against constructed `Ticket` lists: token weighting (id/title beat body/provides), multi-token accumulation, `matchedFields` correctness, blank query → `[]`, stable ordering on ties. Adapter tests: `FakeFileAdapter.search_tickets` returns ranked `SearchHit`s with run-state-resolved summaries; `RealFileAdapter.search_tickets` against `tests/fixtures/projects/with_run_state` finds a term appearing only in a ticket body, and a project with one missing `.md` still returns hits for the rest. Confirm `isinstance(RealFileAdapter(), FileAdapter)` and `isinstance(fake, FileAdapter)` still hold. Keep `file_adapter/` coverage >90% (T35 gate).
