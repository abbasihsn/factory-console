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

Press Ctrl-C to stop. See [`docs/usage.md`](docs/usage.md) for flags, exit codes, and path resolution.

## Docs

- [`docs/usage.md`](docs/usage.md) — install, run, flags, exit codes.
- [`docs/architecture.md`](docs/architecture.md) — the layered CLI → HTTP → Domain → FileAdapter design and its contracts.
- [`docs/contributing.md`](docs/contributing.md) — dev loop, tests, packaging, and release.
- [`docs/planning/`](docs/planning/) — the durable backbone: [`VISION.md`](docs/planning/VISION.md), [`ARCHITECTURE.md`](docs/planning/ARCHITECTURE.md), [`ROADMAP.md`](docs/planning/ROADMAP.md), and the [ticket manifest](docs/planning/tickets.json).

## Status

Planning complete; the MVP is being built ticket-by-ticket from `docs/planning/tickets/mvp/` in dependency order. See [`docs/planning/ROADMAP.md`](docs/planning/ROADMAP.md) for the ladder.

## License

MIT. See [`LICENSE`](LICENSE).
