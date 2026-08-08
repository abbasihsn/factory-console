# Architecture

Factory Console is a single-process local web application. The `factory-console`
CLI resolves a target project path, boots an embedded HTTP server bound to
`127.0.0.1`, opens your default browser, and serves a versioned REST API plus a
pre-built single-page app (SPA) as static assets — all from one installed wheel,
with no Node runtime required at launch. The layers run strictly in one direction:
**CLI → HTTP → Domain → FileAdapter**. The CLI owns path discovery, port
selection, and browser launch; the HTTP server exposes the REST endpoints and
serves the SPA; the domain services (`TicketService`, `DepsService`) hold the read
logic; and two narrow ports are the only layers that touch disk — the read-only
`FileAdapter`, and (since v2) the `FileWriter` behind the token-gated write
endpoints. There is no database and no cache — every request re-reads the project's
own files (`docs/planning/tickets.json`, the ticket `.md` files, `ROADMAP.md`, the
factory's run-state source, and the factory's ledger, results, receipts and
last-stop artefacts under `.factory/`), so the browser always reflects what is on
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
  writing. What may be edited and what may be deleted are two different predicates —
  see the run-state bullet below.
- **FileWriter port** — the write-side `Protocol` the v2 endpoints depend on via
  `Depends()`, paired with the read adapter. Its applies write the ticket `.md`,
  `tickets.json`, and `ROADMAP.md` as one atomic trio; its `preview_*` half is pure.
  Two implementations: `RealFileWriter` (real filesystem) and `FakeFileWriter`
  (in-memory, for tests). It never writes to the factory's run-state source, in any
  of that source's forms.
- **FileAdapter port** — a read-only Python `Protocol` (ten methods, including
  `search_tickets` (best-first; blank query → `[]`), `get_graph` (whole-project
  run-state-coloured dependency DAG; resolved-only edges, self-loops and dangling
  ids omitted), and the pair `read_run_state` / `read_run_states` (one id, and many
  against a single read of the source — the batch form is what keeps a view that asks
  per item, like the roadmap, from re-reading `run-state.json` once per row); all but
  `load_project` take a resolved `Project`) that the backend
  depends on via `Depends()`. Two
  implementations: `RealFileAdapter` (real filesystem) and `FakeFileAdapter`
  (in-memory, for tests).
- **Factory run-state source** (read-only) — the factory-owned artefact the console
  resolves, in probe order `.factory/run-state.json` (what the factory writes), then
  the legacy marker directories `.factory/run-state/` and `docs/planning/.run-state/`.
  The console never writes to it in any form. The factory's own vocabulary is nine
  states — `todo`, `in_progress`, `ready`, `in_part`, `in_submilestone`, `merged`,
  `flagged`, `failed`, `needs_human` (the legacy directory form can additionally spell
  `in-flight`, a name the console coined and the factory never uses). On top of those,
  three answers are the **console's own**, named by no source: `unknown` (no source, a
  source that vanished, or one that resolved and lists nobody — still editable),
  `absent` (a populated source that does not list this ticket — refused an edit, but
  still deletable, because ticket creation is ungated), and `unreadable` (a source that
  is there and could not be read or understood — refused for both edit and delete).
  This corrects an earlier version of this page, which described a directory the factory
  does not write and mapped a listed-nowhere ticket to the editable `todo`; the
  authoritative rules, and the record of the correction, live in
  [`planning/ARCHITECTURE.md`](planning/ARCHITECTURE.md).

  Two more things are read out of the JSON form, both added by App Factory v3 and
  neither of them a state:
  - **`phase`** — a field ON an entry (`building`, `accepting`, `reviewing`, `fixing`,
    `verifying`) saying where a running lane has got to. It qualifies `runState` on the
    ticket views rather than extending the vocabulary above, exactly as the factory
    models it. It is carried as a free string and never validated: a phase is displayed
    and branched on by nothing, so an unrecognised one shows as itself. This is the
    deliberate opposite of an unrecognised **status**, which resolves `unreadable` and
    is refused by both write gates — a cosmetic field must never become a write lockout.
    The legacy marker directories record no phase, which is a complete reading of that
    form and not a gap.
  - **`subversion`** — a top-level record (`branch`, `base_sha`, `name`, `pr_url`) for
    the one sub-version branch the factory is accumulating tickets onto. Under v3 its
    PR is the only recurring human gate, so the shell shows a strip naming it. Absent is
    the normal state between cuts, and only one is ever open.
- **Other factory artefacts** (read-only) — the spend ledger
  (`.factory/metrics/ledger.jsonl`) behind `GET /api/v1/spend`, and per-ticket lane results
  (`.factory/results/<id>.json`) and receipts (`.factory/receipts/<id>.json`) behind
  `GET /api/v1/runs`. `.factory/last-stop.json` has a reader but is not yet surfaced by any
  endpoint. `.factory/` is gitignored, so every one of them is optional and a missing one
  renders as **missing** — never as zero and never as empty.
- **CLI contract** — the `factory-console [PATH] [flags]` surface, its exit codes,
  and its path-resolution rules. See [`usage.md`](usage.md) for the flags.
