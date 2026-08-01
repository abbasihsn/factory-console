# tests/fixtures/projects/

Realistic factory-shaped fixture projects — the executable contract every downstream track tests against. **Read-only** — downstream tests may read but must never mutate.

All three use **camelCase** manifest fields (`dependsOn`), matching the
`ARCHITECTURE.md` data model — not the console repo's own snake_case manifest.
Each manifest carries a top-level `schemaVersion`.

## Layouts (populated by T08)

- `minimal/` — project **trailmark**; 3 tickets (`TM-001` MVP, `TM-015` v1, `TM-028` v2); realistic YAML front-matter + body (two heading levels, list, GFM table, footnote, fenced code); `ROADMAP.md`; NO `.factory/` directory (so run-state probes return `unknown`). Exactly one ticket (`TM-015`) carries an **unknown extra field** (`estimate`) to exercise `Ticket.raw` passthrough.
- `with_run_state/` — project **cadence**; 6 tickets exercising every `RunState`:
  - `todo` (marker present): `CAD-131`, `CAD-140`, `CAD-152` — plain **file** markers.
  - `in-flight`: `CAD-125` — **directory** marker (holds a `state` placeholder).
  - `ready`: `CAD-118` — **directory** marker (holds a `state` placeholder).
  - `merged`: `CAD-100` — plain **file** marker.

  Every ticket in this fixture carries an explicit marker (T80 changed the
  directory form's "present dir, no marker" default from `todo` to `absent` —
  refused — so a fixture ticket relying on that default would now be read-only).

  Markers live at `.factory/run-state/{todo,in-flight,ready,merged}/<id>`, mixing files and directories. `CAD-131` has a `dependsOn` entry (`CAD-207-nonexistent`) pointing to an id absent from the manifest (exercises `unresolvedDeps`); `CAD-140`'s body embeds a literal `<script>alert(1)</script>` snippet to exercise sanitization end-to-end.
- `malformed/` — `docs/planning/tickets.json` with invalid JSON (**trailing comma**, so `json.loads` raises `json.JSONDecodeError`); an otherwise-valid ticket `.md` at `docs/planning/tickets/foo.md`.

> The `with_run_state/.factory/` markers are test data and are force-tracked via
> a negation in the root `.gitignore` (which otherwise ignores `.factory/`).

## Used by

- `tests/unit/` and `tests/integration/` (file-adapter + backend tracks)
- `frontend/tests/e2e/` — Playwright boots the packaged `factory-console` on `with_run_state/` and drives the SPA end-to-end (T33 acceptance harness)
