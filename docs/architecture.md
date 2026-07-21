# Architecture

Factory Console is a single-process local web application. The `factory-console`
CLI resolves a target project path, boots an embedded HTTP server bound to
`127.0.0.1`, opens your default browser, and serves a versioned REST API plus a
pre-built single-page app (SPA) as static assets — all from one installed wheel,
with no Node runtime required at launch. The layers run strictly in one direction:
**CLI → HTTP → Domain → FileAdapter**. The CLI owns path discovery, port
selection, and browser launch; the HTTP server exposes the REST endpoints and
serves the SPA; the domain services (`TicketService`, `DepsService`) hold the read
logic; and a pure, read-only `FileAdapter` port is the only layer that touches
disk. There is no database, no cache, and no file-watcher — every request re-reads
the project's own files (`docs/planning/tickets.json`, the ticket `.md` files,
`ROADMAP.md`, and the factory run-state directory), so the browser always reflects
what is on disk.

For the full backbone — tech stack, data model, cross-cutting concerns, and DevOps
— see [`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md).

## Contracts

The system is defined by four contracts. This page names them; the authoritative
definitions live in [`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md) and are
fleshed out here as each track lands.

- **REST v1** — the versioned JSON API under `http://127.0.0.1:<port>/api/v1`
  (camelCase, ISO-8601, errors as `{ error: { code, message, details? } }`). The
  SPA regenerates its TypeScript types from the published OpenAPI schema.
- **FileAdapter port** — a read-only Python `Protocol` (six methods, each taking a
  `Project`) that the backend depends on via `Depends()`. Two implementations:
  `RealFileAdapter` (real filesystem) and `FakeFileAdapter` (in-memory, for tests).
- **Factory run-state directory** (read-only) — the factory-owned directory the
  console probes to map each ticket to a run-state (`todo` / `in_flight` / `ready`
  / `merged` / `unknown`). The console never writes here.
- **CLI contract** — the `factory-console [PATH] [flags]` surface, its exit codes,
  and its path-resolution rules. See [`usage.md`](usage.md) for the flags.
