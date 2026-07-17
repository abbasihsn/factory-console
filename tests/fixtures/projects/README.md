# tests/fixtures/projects/

Realistic factory-shaped fixture projects — the executable contract every downstream track tests against. **Read-only** — downstream tests may read but must never mutate.

Manifests use the factory contract shape: top-level `schemaVersion` (number) + `tickets` array; each ticket entry has `id, title, status, track, milestone, dependsOn` (camelCase), and list-valued `provides` / `files`. Unknown fields on an entry are preserved (schema-tolerant `Ticket.raw` passthrough).

## Layouts (populated by T08)

- `minimal/` (tickets `TT-1`, `TT-2`, `TT-3`) — 3 tickets across MVP/v1/v2; realistic YAML front-matter + body (heading hierarchy, list, table, footnote, fenced code); `ROADMAP.md`; NO `.factory/` directory (so run-state probes return `unknown`). `TT-2` carries an extra unknown field (`owner`) to exercise `Ticket.raw` passthrough.
- `with_run_state/` (tickets `WS-1`…`WS-6`) — 6 tickets exercising every `RunState`:
  - `WS-1`, `WS-2` → `todo` — markers are **plain files** `.factory/run-state/todo/WS-1`, `…/WS-2`.
  - `WS-3` → `in-flight` — marker is a **directory** `.factory/run-state/in-flight/WS-3/` (holds `.gitkeep`). `WS-3` also `dependsOn` the unknown id `WS-404` (not in the manifest) to exercise `unresolvedDeps`.
  - `WS-4` → `ready` — marker is a **directory** `.factory/run-state/ready/WS-4/` (holds `.gitkeep`). Its body embeds a literal `<script>alert(1)</script>` snippet to exercise HTML sanitization end-to-end.
  - `WS-5` → `merged` — marker is a **plain file** `.factory/run-state/merged/WS-5`.
  - `WS-6` → present in the manifest but with **no marker** on disk, so it resolves to `todo` (the "present run-state dir but missing marker → todo" rule).
- `malformed/` (ticket `foo`) — `docs/planning/tickets.json` with invalid JSON (a trailing comma) so `json.loads` raises; an otherwise-valid `docs/planning/tickets/foo.md`.

## Tracking the fixture `.factory/` markers

The repo-root `.gitignore` ignores `.factory/`. A scoped negation immediately after that line (`!tests/fixtures/**/.factory/` then `!tests/fixtures/**/.factory/**`) re-includes these fixture run-state markers so they are committed as part of the contract. Do not remove it, or `with_run_state` loses its run-state coverage.

## Used by

- `tests/unit/` and `tests/integration/` (file-adapter + backend tracks)
- `frontend/tests/e2e/` — Playwright boots the packaged `factory-console` on `with_run_state/` and drives the SPA end-to-end (T33 acceptance harness)
