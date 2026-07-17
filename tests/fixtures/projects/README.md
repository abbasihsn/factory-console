# tests/fixtures/projects/

Realistic factory-shaped fixture projects — the executable contract every downstream track tests against. **Read-only** — downstream tests may read but must never mutate.

## Layouts (populated by T08)

- `minimal/` (project "Pocket Ledger") — 3 tickets (`T01`, `T07`, `T12`) across MVP/v1/v2; realistic YAML front-matter + body (heading, list, table, footnote, fenced code); `ROADMAP.md`; NO `.factory/` directory (so run-state probes return `unknown`). `T01` carries an unknown extra field (`estimate`) to exercise `Ticket.raw` passthrough.
- `with_run_state/` (project "Snip") — 6 tickets (`S01`–`S06`) exercising every `RunState`: `S01`/`S02` todo (marker files), `S03` in-flight (marker dir), `S04` ready (marker dir), `S05` merged (marker file), `S06` present-but-no-marker (= todo). Marker files and directories are mixed per the run-state contract (`todo/`, `merged/` hold files; `in-flight/`, `ready/` hold directories with a `.gitkeep`). `S03` has a `dependsOn` edge to `S99`, absent from the manifest (exercises `unresolvedDeps`). `S04`'s body embeds a `<script>alert(1)</script>` snippet to exercise HTML sanitization end-to-end.
- `malformed/` — `docs/planning/tickets.json` with invalid JSON (a trailing comma, so `json.loads` raises); an otherwise-valid `docs/planning/tickets/foo.md`.

## Used by

- `tests/unit/` and `tests/integration/` (file-adapter + backend tracks)
- `frontend/tests/e2e/` — Playwright boots the packaged `factory-console` on `with_run_state/` and drives the SPA end-to-end (T33 acceptance harness)
