# [T72] Property-based tests: write-safety invariant (RunStateGate + atomic co-writer) via Hypothesis

milestone: v2 · track: testing · depends_on: T35, T56, T59, T60, T61 · provides: Hypothesis property tests pinning the flagship v2 invariant — no write mutates a non-todo ticket, and the co-writer is all-or-nothing.

## Context

The single most important v2 safety guarantee is that the console can NEVER mutate a ticket that is not editable (RunState not in {todo, unknown}) and can never leave a partially-written trio of files (tickets.json manifest, the ticket `.md`, ROADMAP.md). Example-based tests miss adversarial inputs; this ticket adds generative (property-based) coverage that hammers the `RunStateGate` and the atomic co-writer across randomized run-states, ticket IDs, and payloads. It is the flagship v2 test ticket and the acceptance evidence for the immutability rule in VISION.md/ARCHITECTURE.md.

## Staged approach

1. Add `hypothesis>=6` to the `[project.optional-dependencies].dev` list in `pyproject.toml` (append only; do not touch other extras).
2. Create `tests/unit/test_write_safety_properties.py`.
3. Property A (gate): with a strategy drawing a `RunState` from the full enum and a mutation kind (create/edit/delete), build a `FakeFileWriter`/`FakeFileAdapter` stack and assert that whenever RunState is NOT todo/unknown the write is rejected with `TicketNotMutable` and the FakeFileAdapter records ZERO mutating calls; when todo/unknown it is allowed.
4. Property B (atomicity): drive the co-writer against a `tmp_path` RealFileWriter fixture, injecting a failure at each stage of the manifest/md/roadmap sequence via a strategy over failure points, and assert the on-disk trio is left byte-for-byte unchanged (snapshot compare) — never a half-applied state; and on success all three are updated consistently.
5. Reuse the seeded-Ticket helpers from `tests/integration/test_api_tickets.py`; keep strategies small and `@settings(max_examples=...)` modest so the suite stays fast under the CI matrix. Standalone test module (no re-exports).

## Critical files

- `pyproject.toml`
- `tests/unit/test_write_safety_properties.py` (new)

## Interface & data

Consumes (by reference): the v2 `FileWriter` port (T60), the `RunStateGate` (T56), the atomic co-writer (T59), and `RealFileWriter` (T61); the `RunState` enum from `domain/run_state.py`; `TICKET_ID_PATTERN` from `domain/ticket.py` for a valid-id strategy; `FakeFileAdapter`/`RealFileAdapter`. `pyproject.toml` gains one dependency line (`hypothesis>=6`) in the dev extra. No DB. NFR: pins the idempotency/atomicity + run-state-authorization invariants.

## Verification

`pip install -e '.[dev]'` then `pytest -q tests/unit/test_write_safety_properties.py`; full gate via `pytest -q --cov=factory_console` staying >=85%. No network, no real project touched (tmp_path + FakeFileAdapter only).
