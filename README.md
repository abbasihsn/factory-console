# Factory Console

A standalone local console that points at any App-Factory-generated project directory and lets you browse its tickets — status, title, description, and dependencies. Read-only in MVP; safe editing of `todo` tickets in v2.

One command from any factory project directory:

```
factory-console
```

Opens a local browser tab (bound to `127.0.0.1`, no server infra, no cloud coupling) showing:

- A searchable, filterable list of tickets.
- A detail view with the rendered ticket `.md`, resolved `depends_on` / `provides`, and factory run-state badge.
- A dep-neighborhood view showing direct deps and dependents as clickable lists.

## Status

Planning complete. Foundation in progress — build the MVP tickets in `docs/planning/tickets/mvp/` in dependency order using `/ai-gh-orchestrate-plan`.

## Quickstart

Not yet — the repo is a scaffold. Install and dev-loop instructions land with the Python package skeleton (T02) and the dev/package scripts (T09).

## Roadmap

- **MVP** — read-only browsing (list + detail + dep neighborhood).
- **v1** — rendered dependency graph, roadmap view, cross-ticket search, file-watcher live updates.
- **v2** — safe editing of `todo` tickets (in-flight / ready / merged remain read-only).

Full detail in [`docs/planning/ROADMAP.md`](docs/planning/ROADMAP.md).

## Docs

- [`docs/planning/VISION.md`](docs/planning/VISION.md) — problem, users, value prop, constraints.
- [`docs/planning/ARCHITECTURE.md`](docs/planning/ARCHITECTURE.md) — architecture, tech stack, data model, contracts.
- [`docs/planning/PROJECT_STRUCTURE.md`](docs/planning/PROJECT_STRUCTURE.md) — the directory tree.
- [`docs/planning/ROADMAP.md`](docs/planning/ROADMAP.md) — milestones and the MVP ticket ladder.
- [`docs/planning/tickets.json`](docs/planning/tickets.json) — machine-readable manifest.
- [`docs/planning/tickets/mvp/`](docs/planning/tickets/mvp/) — one PR-sized plan per MVP ticket.

## License

MIT. See [`LICENSE`](LICENSE).
