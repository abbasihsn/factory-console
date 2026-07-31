# [T78] Read the run-state the factory actually writes, and model the states it actually has

milestone: v2.1 · track: file-adapter · depends_on: — · provides: a `RunStateSource` that resolves either `.factory/run-state.json` or the legacy marker-directory form and reports WHICH it resolved; a `RunState` enum covering the factory's real nine-state vocabulary; and an explicit alias table mapping factory status to console state.

## Context

Two mismatches, both measured, both invisible to the current tests.

**Layout.** `file_adapter/run_state.py` probes for a run-state *directory* of per-state marker subdirectories, and `docs/architecture.md` documents that layout. The factory writes neither. It writes one JSON file, `.factory/run-state.json`: `{"version": 1, "tickets": {"T01": {"status": "merged", "pr_url": null}, ...}, "parts_landed": {...}}`. Run against this repository with the console's own adapter, `find_run_state_dir()` returns `None` and all 77 tickets resolve to `RunState.unknown`, while the JSON records 73 of them `merged`.

**Vocabulary.** `RunState` has five members — `todo`, `in-flight`, `ready`, `merged`, `unknown`. The factory's own `FAC_STATES` is nine: `todo in_progress ready in_part in_submilestone merged flagged failed needs_human`. Only three overlap. **There is no `in_flight` in the factory at all** — the console invented it; the factory's in-progress state is `in_progress`, and it has three failure-ish states (`flagged`, `failed`, `needs_human`) that the console cannot represent and that an operator would most want to see.

The existing tests pass because their fixtures build the directory form with the state names the code expects. They confirm code-matches-spec; the spec is what is wrong. **A fixture written from the same assumption as the code under test cannot detect that the assumption is wrong** — so this ticket's tests must be built from the factory's shape, not the console's.

The directory form is NOT deleted. Nothing here shows it is unused elsewhere, and dropping a read path on the evidence of one repository repeats the mistake in the other direction. It becomes the second-precedence source, and which source was resolved becomes an observable fact rather than a guess.

## Staged approach

1. Extend `domain/run_state.py`'s `RunState` with the factory statuses it cannot currently express: `in_progress = "in_progress"`, `in_part = "in_part"`, `in_submilestone = "in_submilestone"`, `flagged = "flagged"`, `failed = "failed"`, `needs_human = "needs_human"`. Keep every existing member and value — `in-flight` stays, because the directory form still maps to it and the pinned value test must keep passing. The docstring's "values mirror the on-disk directory names" rule is now true of only the directory source, so restate it: **the value mirrors whichever source named it**, and the alias table in step 3 is the single place any name is interpreted.
2. Add `domain/run_state_source.py`: frozen `RunStateSource` with `kind: Literal["json", "directory"]` and `path: Path`, plus `RUN_STATE_SOURCE_LOCATIONS` giving probe order highest-first — `.factory/run-state.json` (json), `.factory/run-state/` (directory), `docs/planning/.run-state/` (directory). The JSON file wins because it is what the factory writes today.
3. In `file_adapter/run_state.py` add `find_run_state_source(project_root) -> RunStateSource | None`, requiring the correct node type at each location (`is_file` for json, `is_dir` for directory) so a stray file where a directory is expected is not accepted, and vice versa. Keep `find_run_state_dir` as a wrapper returning only directory sources, so existing callers and their tests keep working unchanged.
4. Add `read_json_run_state(path) -> JsonRunState`: parse, read `.tickets`, and map each `status` through an **explicit alias table** keyed by the factory's nine `FAC_STATES` values. Never by string munging — the underscore/hyphen difference between `in_progress` and `in-flight` is exactly the kind of thing a `.replace()` gets away with until it doesn't. A status absent from the table maps to `unknown` **and** is collected into `unrecognised: list[str]`; it is never silently dropped, because a factory that gains a tenth state must be visible as a gap rather than as a repo full of `unknown`.
5. Add `probe_ticket_state_from_source(source, ticket_id)` dispatching on `source.kind`, and route `RealFileAdapter.read_run_state` through it. A malformed JSON file — unparseable, `tickets` absent, `tickets` not an object — must NOT fail the request: it yields `unknown` for every ticket and is surfaced as a source-level problem. A broken run-state file is the one case where "I could not tell" is the honest answer.
6. Widen `Project`: keep `runStateDir: Path | None` unchanged in meaning (a path only when the resolved source is a directory) and add `runStateSource: RunStateSource | None`, set in `file_adapter/discovery.py`.
7. Frontend: `RunState` is generated from the OpenAPI schema, so the new members arrive in the type automatically — update `RunStateBadge.svelte` to render them (the three failure-ish states visually distinct from the in-progress ones) and check every `RunState` switch/map for a stale branch set. `isEditable` is an allowlist of `todo`/`unknown` and so treats all six new states as read-only without change — assert that rather than assuming it.
8. Preserve the read-only guarantee: `run_state.py` still contains no filesystem-mutating call and the existing guard test must still pass over the new code.

## Critical files

- `server/factory_console/domain/run_state.py`
- `server/factory_console/domain/run_state_source.py` (new)
- `server/factory_console/file_adapter/run_state.py`
- `server/factory_console/file_adapter/discovery.py`
- `server/factory_console/domain/project.py`
- `frontend/src/lib/components/RunStateBadge.svelte`

## Interface & data

`RunStateSource(kind: Literal["json","directory"], path: Path)`, frozen, `extra="forbid"`. `find_run_state_source(project_root: Path) -> RunStateSource | None`. `read_json_run_state(path: Path) -> JsonRunState` carrying `states: dict[str, RunState]` and `unrecognised: list[str]`. `probe_ticket_state_from_source(source: RunStateSource | None, ticket_id: str) -> RunState` — a `None` source yields `unknown`, unchanged from today. `Project` gains `runStateSource: RunStateSource | None = None`. On-disk contract consumed read-only, never written: `.factory/run-state.json` = `{"version": int, "tickets": {TICKET_ID: {"status": str, "pr_url": str|null}}, "parts_landed": object}`; status vocabulary = `FAC_STATES` (`todo in_progress ready in_part in_submilestone merged flagged failed needs_human`). NFR: read-only (guard test); no crash on malformed input; ids validated against `TICKET_ID_PATTERN` and `PathTraversal` before any path join.

## Verification

Pytest `test_run_state_source.py`. **The fixture must be a copy of a real factory `run-state.json`, not one written to match this code** — assert `merged` for a ticket the factory marked merged; a test that asserts `unknown` there is asserting the bug. Cases: probe order (json beats directory when both exist; directory when only it exists; `None` when neither); wrong node type at a location rejected; all nine `FAC_STATES` values map through the alias table with no `unknown` among them; a tenth invented status yields `unknown` AND appears in `unrecognised`; malformed JSON, missing `tickets`, and `tickets` as a list each yield `unknown` for every ticket without raising. Domain: the pinned `RunState` value test extended to the new members. Existing `test_run_state.py` stays green **unmodified** — that is the compatibility claim, and editing it to pass would void it. Vitest: `RunStateBadge` renders each new state; `isEditable` returns `false` for all six new states. `make lint`, `pytest`, `pnpm check`, `pnpm test` green.
