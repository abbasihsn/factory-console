# Architecture

Factory Console is a single-process local web application. The `factory-console`
CLI resolves a target project path, boots an embedded HTTP server bound to
`127.0.0.1`, opens your default browser, and serves a versioned REST API plus a
pre-built single-page app (SPA) as static assets — all from one installed wheel,
with no Node runtime required at launch. The layers run strictly in one direction:
**CLI → HTTP → Domain → FileAdapter**. The CLI owns path discovery, port
selection, and browser launch; the HTTP server exposes the REST endpoints and
serves the SPA; the domain services (`TicketService`, `DepsService`, `RunService`)
hold the read logic; and three narrow ports are the only layers that touch disk —
the read-only `FileAdapter`, (since v2) the `FileWriter` behind the token-gated
write endpoints, and (since v2.1) the read-only `RunArtifactReader`. There is no
database and no cache — every request re-reads the project's own files
(`docs/planning/tickets.json`, the ticket `.md` files, `ROADMAP.md`, and the
factory's `.factory/` run artifacts), so the browser always reflects what is on
disk.

For the full backbone — tech stack, data model, cross-cutting concerns, and DevOps
— see [`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md).

## Contracts

The system is defined by six contracts. This page names them; the authoritative
definitions live in [`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md) and are
fleshed out here as each track lands.

- **REST v1** — the versioned JSON API under `http://127.0.0.1:<port>/api/v1`
  (camelCase, ISO-8601, errors as `{ error: { code, message, details? } }`). The
  SPA regenerates its TypeScript types from the published OpenAPI schema. Reads are
  open; the v2 write verbs (`POST` / `PUT` / `DELETE /api/v1/tickets`) require the
  per-session write token in an `X-Factory-Write-Token` header, each return the one
  `WriteResult` diff envelope, and each honour `?dryRun=true` to preview without
  writing. Only `todo` tickets may be edited or deleted. Since v2.1,
  `GET /api/v1/runs` and `GET /api/v1/runs/{id}` report what the factory did, one
  record per manifest ticket; each record's `unavailable` names the artifact
  sources that did not answer for it, so every null is attributable.
- **FileWriter port** — the write-side `Protocol` the v2 endpoints depend on via
  `Depends()`, paired with the read adapter. Its applies write the ticket `.md`,
  `tickets.json`, and `ROADMAP.md` as one atomic trio; its `preview_*` half is pure.
  Two implementations: `RealFileWriter` (real filesystem) and `FakeFileWriter`
  (in-memory, for tests). It never writes under the run-state directory.
- **FileAdapter port** — a read-only Python `Protocol` (eight methods, including
  `search_tickets` (best-first; blank query → `[]`) and `get_graph` (whole-project
  run-state-coloured dependency DAG; resolved-only edges, self-loops and dangling
  ids omitted); all but `load_project` take a resolved `Project`) that the backend
  depends on via `Depends()`. Two
  implementations: `RealFileAdapter` (real filesystem) and `FakeFileAdapter`
  (in-memory, for tests).
- **RunArtifactReader port** — a read-only `Protocol` (five methods, all taking a
  resolved `Project`) covering the factory's per-run `.factory/` artifacts that
  `FileAdapter` does not: lane results, receipts, last-stop, and the `pr_url` half
  of run-state. A narrow sibling port rather than a ninth `FileAdapter` method, so
  one surface's needs do not widen every implementation. The two per-ticket reads
  are batched, so each artifact directory is resolved once per request. Two
  implementations: `RealRunArtifactReader` and `FakeRunArtifactReader`.
- **Factory run-state directory** (read-only) — the factory-owned directory the
  console probes to map each ticket to a run-state (`todo` / `in_flight` / `ready`
  / `merged` / `unknown`). The console never writes here.
- **CLI contract** — the `factory-console [PATH] [flags]` surface, its exit codes,
  and its path-resolution rules. See [`usage.md`](usage.md) for the flags.
