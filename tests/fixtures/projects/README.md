# tests/fixtures/projects/

Realistic factory-shaped fixture projects — the executable contract every downstream track tests against. **Read-only** — downstream tests may read but must never mutate.

## Layouts (populated by T08)

- `minimal/` — 3 tickets across MVP/v1/v2; realistic YAML front-matter + body (headings, list, table, footnote, fenced code); `ROADMAP.md`; NO `.factory/` directory (so run-state probes return `unknown`).
- `with_run_state/` — 6 tickets exercising every `RunState` (2 todo, 1 in-flight, 1 ready, 1 merged, 1 present-but-no-marker = todo); `.factory/run-state/{todo,in-flight,ready,merged}/<id>` mixing files and directories; one ticket with `dependsOn` pointing to an unknown id (exercises `unresolvedDeps`); one ticket body includes a `<script>alert(1)</script>` snippet to exercise sanitization end-to-end.
- `malformed/` — `docs/planning/tickets.json` with invalid JSON (trailing comma); an otherwise-valid ticket `.md`.

## Used by

- `tests/unit/` and `tests/integration/` (file-adapter + backend tracks)
- `frontend/tests/e2e/` — Playwright boots the packaged `factory-console` on `with_run_state/` and drives the SPA end-to-end (T33 acceptance harness)
