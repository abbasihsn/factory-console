# Factory Console

[![CI](https://github.com/abbasihsn/factory-console/actions/workflows/ci.yml/badge.svg)](https://github.com/abbasihsn/factory-console/actions/workflows/ci.yml)

A standalone local console that points at any App-Factory-generated project directory and lets you browse its tickets — status, title, description, and dependencies. Read-only in the MVP; safe editing of `todo` tickets in v2.

## Install

Factory Console is a Python wheel on PyPI. Run it with no install via `uvx`, or install it with `pipx`:

```
uvx factory-console            # run without installing
pipx install factory-console   # install onto your PATH
```

## Quickstart

From any App Factory project directory:

```
cd my-factory-project
factory-console
```

Within a few seconds the console discovers the project, starts a local server on `127.0.0.1`, and prints the URL to open in your browser (no cloud, no server infra). The UI shows:

- A searchable, filterable list of every ticket (id / status / title / track).
- A detail view with the rendered ticket `.md`, resolved `depends_on` / `provides`, and a factory run-state badge.
- A dependency-neighborhood view listing direct deps and dependents as clickable links.
- A **global full-text search** box (header) over a ticket's id, title, `provides`, and body, with results at `/search`.
- A **dependency graph** (`/graph`) — the whole project as a run-state-colored DAG; click a node to open its ticket.
- A **roadmap** (`/roadmap`) rendering the project's `ROADMAP.md` as milestone sections.
- **Live updates**: open pages auto-refresh over SSE when a ticket's run-state changes on disk, with a status indicator pill (and graceful fallback to the Reload button).

Press Ctrl-C to stop. See [`docs/usage.md`](docs/usage.md) for flags, exit codes, and path resolution.

## Screenshots

Captured from the real UI by the Playwright screenshots pipeline against the `with_run_state` fixture.

![Ticket list](docs/screenshots/list.png)

_The searchable ticket list at `/`._

![Ticket detail](docs/screenshots/detail.png)

_The `CAD-125` detail view with rendered body, deps, and run-state badge._

![Dependency neighborhood](docs/screenshots/deps.png)

_The `CAD-125` dependency neighborhood listing its direct deps._

![Global search results](docs/screenshots/search.png)

_Full-text search for `idempotent` at `/search`, matching two ticket bodies._

![Dependency graph](docs/screenshots/graph.png)

_The `/graph` dependency DAG, nodes colored by factory run-state._

![Roadmap](docs/screenshots/roadmap.png)

_The `/roadmap` milestone view rendered from the project's `ROADMAP.md`._

![Live-update indicator](docs/screenshots/live.png)

_The live-update pill in its connected `Live` state._

Regenerate with `pnpm --dir frontend screenshots` (equivalently `pnpm --dir frontend e2e --grep screenshots && node frontend/scripts/copy-screenshots.mjs`).

## Docs

- [`docs/usage.md`](docs/usage.md) — install, run, flags, exit codes.
- [`docs/architecture.md`](docs/architecture.md) — the layered CLI → HTTP → Domain → FileAdapter design and its contracts.
- [`docs/contributing.md`](docs/contributing.md) — dev loop, tests, packaging, and release.
- [`docs/planning/`](docs/planning/) — the durable backbone: [`VISION.md`](docs/planning/VISION.md), [`ARCHITECTURE.md`](docs/planning/ARCHITECTURE.md), [`ROADMAP.md`](docs/planning/ROADMAP.md), and the [ticket manifest](docs/planning/tickets.json).

## Status

Planning complete; the MVP is being built ticket-by-ticket from `docs/planning/tickets/mvp/` in dependency order. See [`docs/planning/ROADMAP.md`](docs/planning/ROADMAP.md) for the ladder.

## License

MIT. See [`LICENSE`](LICENSE).
