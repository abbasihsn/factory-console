# [T79] Ledger reader — typed spend records from `.factory/metrics/ledger.jsonl`

milestone: v3 · track: file-adapter · depends_on: — · provides: a read-only `read_ledger()` that parses `.factory/metrics/ledger.jsonl` into typed `LedgerEntry` records, tolerates partial and unparseable lines without discarding the file, and distinguishes "no ledger" from "an empty ledger".

## Context

The factory appends one JSON object per lane to `.factory/metrics/ledger.jsonl`. A real entry from this repository:

```json
{"ts":"2026-07-30T16:33:22Z","agent":"lead","level":"ticket","ids":["T71"],
 "model":"sonnet","effort":"medium","wall_min":12,"turns":27,"peak_context":133027,
 "tokens":{"input":8546,"output":40143,"cache_read":7261803,"cache_creation":232826,"total":7543318},
 "cost_usd":5.740558350000003,"cost_scope":"lane","session_id":"81dda660-...",
 "review_tier":null,"sessions":1,
 "by_model":{"claude-haiku-4-5-20251001":{...},"claude-sonnet-5":{...},"claude-opus-4-8[1m]":{...}}}
```

The console shows none of it. This ticket is the read half only — no endpoint, no view; T82 and T84 build on it. It shares no file with T78 or T85 and can run concurrently with either.

Two properties matter more than the parsing. **A JSONL file is appended to by a live writer**, so the last line may be a partial write at the moment the console reads it — that must cost one entry, not the file. And **a missing ledger is not a zero bill**: `.factory/` is gitignored (`.gitignore:40`), so a fresh clone has no ledger at all, and a reader that returns `[]` for both cases lets the UI above it truthfully render "this cost nothing". The result type must make the two impossible to confuse.

## Staged approach

1. Add `server/factory_console/domain/ledger.py`: frozen `LedgerEntry` with the fields above — `ts: datetime`, `agent: str`, `level: str`, `ids: list[str]`, `model: str | None`, `effort: str | None`, `wall_min: float | None`, `turns: int | None`, `tokens: TokenCounts`, `cost_usd: float`, `cost_scope: str | None`, `session_id: str | None`, `review_tier: str | None`, `by_model: dict[str, ModelSpend]`. Use `extra="ignore"`, NOT `extra="forbid"`: the ledger is written by another program on its own release cycle, and a field the factory adds tomorrow must not make today's console refuse the file. This is the opposite choice from the console's own models and the docstring must say why.
2. Add `server/factory_console/file_adapter/ledger.py` with `find_ledger_path(project_root) -> Path | None` (probing `.factory/metrics/ledger.jsonl`) and `read_ledger(path) -> LedgerRead`.
3. `LedgerRead` is the absence-carrying result: `entries: list[LedgerEntry]`, `skipped: list[SkippedLine]` (line number + reason), and the source path. The *caller* distinguishes no-ledger from empty-ledger by receiving `None` from `find_ledger_path` versus a `LedgerRead` with zero entries — those are different values, and no code path may collapse them.
4. Parse line by line. A line that is not valid JSON, or is valid JSON that fails validation, is recorded in `skipped` with its line number and the reason, and parsing continues. Never abort the file on one bad line, and never drop one silently.
5. Handle the live-append case explicitly: a trailing line without a terminating newline is attempted like any other, and if it fails it lands in `skipped` as `partial_line`. Do not special-case by position — a bad line in the middle and a truncated line at the end deserve the same treatment, and guessing which is which from position is not something a reader can know.
6. Guard the file's read-only status the same way `run_state.py` is guarded: a test asserting `ledger.py` contains no filesystem-mutating call.

## Critical files

- `server/factory_console/domain/ledger.py` (new)
- `server/factory_console/file_adapter/ledger.py` (new)

## Interface & data

`find_ledger_path(project_root: Path) -> Path | None`. `read_ledger(path: Path) -> LedgerRead`. `LedgerRead(path: Path, entries: list[LedgerEntry], skipped: list[SkippedLine])`; `SkippedLine(line_no: int, reason: Literal["not_json","invalid_entry","partial_line"], excerpt: str)` — the excerpt truncated and never containing a full session id. `TokenCounts(input, output, cache_read, cache_creation, total)` all `int` defaulting to `0`; `ModelSpend(input, output, cache_read, cache_creation, cost_usd)`. Models are `extra="ignore"` by deliberate exception. On-disk contract consumed read-only: `.factory/metrics/ledger.jsonl`, one JSON object per line, appended by the factory. NFR: read-only (guard test); no unbounded read — cap the file at a documented size and record the cap as a `SkippedLine` reason rather than truncating silently; `session_id` is not surfaced to any API layer by this ticket.

## Verification

Pytest `test_ledger.py`. **The primary fixture is a verbatim copy of real lines from this repo's ledger**, not a hand-written minimal object — the point of the ticket is reading what the factory writes. Cases: a real multi-model entry parses with `by_model` intact and `cost_usd` exact; an entry carrying an unknown top-level field parses (that is what `extra="ignore"` buys, and a test that omits it would let a future `forbid` slip in); a not-JSON line, an invalid-entry line, and a truncated final line each appear in `skipped` with the right reason while every good line still parses; **`find_ledger_path` returning `None` and `read_ledger` returning zero entries are asserted to be distinguishable values** — this is the absence rule and it is the assertion most likely to be quietly dropped; the size cap produces a recorded reason, not a silent short read. Guard test for no-writes. `make lint`, `pytest` green.
