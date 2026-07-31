# [T82] `GET /api/v1/spend` — what the factory cost

milestone: v2.1 · track: backend · depends_on: T79 · provides: a read-only spend endpoint aggregating the ledger by ticket, by model and by agent level, reporting skipped lines and the no-ledger case explicitly.

## Context

T79 reads `.factory/metrics/ledger.jsonl` into typed entries. This turns them into the three cuts an operator actually asks for — what did this ticket cost, where did the money go by model, and how much of it was review rather than build — and exposes them under the existing API seam.

Real magnitudes from this repository, so the view above this is designed for the right numbers: one ticket lane ranged **$1.40 to $5.74**, 27 turns, 4–12 wall minutes, and single entries mix three models (`claude-haiku-4-5`, `claude-sonnet-5`, `claude-opus-4-8[1m]`) inside one `by_model` object. Totals are dollars, not cents, and per-model breakdowns are the interesting part rather than a detail.

Two things this endpoint must not do. It must not treat a missing ledger as zero — `.factory/` is gitignored, so that is the *normal* state of a fresh clone, and a "$0.00 total" there is a false statement about a real cost. And it must not silently exclude skipped lines from the totals: a total computed over 40 of 43 entries is wrong by an unknown amount, and the response has to carry that.

An entry's `ids` field is a **list** — a lane may cover more than one ticket. Attribution therefore has a choice to make, and the choice must be stated rather than fall out of the code: this ticket attributes an entry's full cost to every id it names and reports the per-ticket figures as *attributed* cost, with the sum of attributed costs allowed to exceed the true total. The alternative — splitting evenly — invents a precision the ledger does not have. The response names which rule was used.

## Staged approach

1. Add `server/factory_console/domain/spend.py`: `SpendTotals(costUsd, entries, tokens: TokenCounts)`; `TicketSpend(ticketId, attributedCostUsd, entries, models: list[str])`; `ModelSpend(model, costUsd, tokens)`; `LevelSpend(level, costUsd, entries)` keyed by the ledger's `level` field (`ticket`, and whatever else appears — do not hardcode a closed set, since the factory owns the vocabulary).
2. Add `server/factory_console/domain/spend_calc.py` as a **pure** aggregator: `aggregate(entries: list[LedgerEntry]) -> SpendReport`. No I/O, no `Project`, no paths. Keeping it pure is what makes the attribution rule testable directly instead of through an HTTP round-trip — the same reason this program made its other predicates injectable rather than reachable only through their callers.
3. Sum `cost_usd` in a way that does not accumulate float error across hundreds of entries, and round only at the response boundary. Never round per entry and then sum.
4. Add `api/v1/spend.py` with `GET /api/v1/spend`, registered by one `include_router` line in `api/v1/__init__.py`.
5. The response carries `source: {"found": bool, "path": str | null}` and `skipped: [{"lineNo": int, "reason": str}]`. `found: false` is the fresh-clone case and the client must be able to act on it without inspecting whether `totals.costUsd` is zero — **a zero total and an unread ledger must not be the same response.**
6. Never surface `session_id` from the ledger, and keep `by_model` keys as the factory wrote them (they are model ids, not display names; mapping them is the view's job, and a mapping applied here would hide a model the console has not heard of).

## Critical files

- `server/factory_console/domain/spend.py` (new)
- `server/factory_console/domain/spend_calc.py` (new)
- `server/factory_console/api/v1/spend.py` (new)
- `server/factory_console/api/v1/__init__.py`

## Interface & data

`GET /api/v1/spend` → `{"source": SourceInfo, "attribution": "full-to-each-id", "totals": SpendTotals, "byTicket": [TicketSpend], "byModel": [ModelSpend], "byLevel": [LevelSpend], "skipped": [SkippedLineInfo]}`. `aggregate(entries: list[LedgerEntry]) -> SpendReport` is pure and total — an empty list is a valid input yielding zeroed totals, and the caller, not this function, knows whether the list is empty because the ledger was empty or because it was missing. `SkippedLineInfo(lineNo, reason)` — the excerpt from T79 is not exposed over HTTP. Errors via the existing `ApiError` envelope. NFR: read-only; no session ids in responses; response size bounded by ticket and model counts, not by ledger length.

## Verification

Pytest `test_spend_calc.py` on the pure aggregator, fixtures taken from **real ledger lines**: a multi-model entry contributes to `byModel` under each of its `by_model` keys with the factory's exact cost figures; a multi-id entry contributes its full cost to each id and the doc'd attribution rule is asserted by name; totals over the real file match a figure computed independently with `jq` — an aggregate checked only against itself proves arithmetic, not correctness. `test_api_spend.py`: **a project with no ledger returns `source.found == false`, and that response is asserted to be distinguishable from a project with an empty ledger** (both have zero totals, and a test that checks only the totals passes on the bug); skipped lines from T79 appear in the response so a partial total is visibly partial; no `session_id` appears anywhere in the serialized response — assert over the whole body, not per-field. `make lint`, `pytest` green.
