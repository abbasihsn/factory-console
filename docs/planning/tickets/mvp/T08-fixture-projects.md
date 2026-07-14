# [T08] Fixture projects (minimal, with_run_state, malformed)

milestone: MVP · track: file-adapter · depends_on: T01 · provides: Shared executable test contract at `tests/fixtures/projects/{minimal, with_run_state, malformed}/` — consumed by file-adapter unit + integration tests, backend integration tests, and frontend Playwright e2e (`with_run_state` IS the acceptance harness)

## Context

The three fixture projects encode the shape of a real factory-generated project. Content must be realistic (not synthetic one-liners) so tests catch regressions. Ship early so parser tickets and backend endpoint tickets can run against them in parallel.

## Staged approach

1. `minimal/`: `docs/planning/tickets.json` (schemaVersion + 3 tickets across MVP/v1/v2, each with `id/title/status/track/milestone/dependsOn/provides/files`, one including an unknown extra field to exercise `raw` passthrough); `docs/planning/tickets/<id>.md` for each with realistic YAML front-matter + body (headings, list, table, footnote, fenced code); `ROADMAP.md`; NO `.factory/` dir (so run-state probes return `unknown`).
2. `with_run_state/`: same shape but 6 tickets exercising every `RunState` (2 `todo`, 1 `in-flight`, 1 `ready`, 1 `merged`, 1 present-but-no-marker=`todo`); include `.factory/run-state/{todo,in-flight,ready,merged}/<id>` mixing files and directories; include one ticket with `dependsOn -> unknown-id` to exercise `unresolvedDeps`; include a `<script>alert(1)</script>` snippet in one ticket body to exercise sanitization end-to-end.
3. `malformed/`: `docs/planning/tickets.json` with invalid JSON (trailing comma); an otherwise-valid `docs/planning/tickets/foo.md`.
4. `tests/fixtures/projects/README.md` documenting the contract (which invariant each project encodes; downstream tests MAY read but MUST NOT write).
5. `tests/fixtures/__init__.py`.
6. `tests/unit/test_fixtures_shape.py`: `minimal` has 3 tickets; `with_run_state` has 6 tickets + markers for every enum; `malformed`'s `tickets.json` fails `json.loads`.

## Critical files

- `tests/fixtures/projects/minimal/`
- `tests/fixtures/projects/with_run_state/`
- `tests/fixtures/projects/malformed/`
- `tests/fixtures/projects/README.md`
- `tests/fixtures/__init__.py`
- `tests/unit/test_fixtures_shape.py`

## Interface & data

Encodes `ARCHITECTURE.md` data_model + run-state contract as executable examples. NFR: read-only fixtures (tests must not mutate).

## Verification

`pytest tests/unit/test_fixtures_shape.py -q` green; `python -c 'import json,pathlib; json.loads(pathlib.Path("tests/fixtures/projects/minimal/docs/planning/tickets.json").read_text())'` succeeds; same for `with_run_state`; same for `malformed` FAILS; `find tests/fixtures/projects/with_run_state/.factory/run-state -mindepth 1` shows markers across all four states.
