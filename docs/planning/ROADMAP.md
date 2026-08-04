# Roadmap — Factory Console

Rolling wave: MVP fully ticketed; v1–v3 are epic-level until elaborated just-in-time.

## At a glance — features by version

```mermaid
timeline
    title Factory Console — features by version
    MVP : Ticket list & detail : Dependency neighborhood : Local read-only viewer (127.0.0.1)
    v1 : Rendered dependency graph : Full-text search : File-watcher live updates : Roadmap/milestone view
    v2 : Safe editing of todo tickets : Manifest + roadmap co-writer : Loopback write token
    v2.1 : Real run-state source & vocabulary : Corrected write gate : Runs view : Spend view : One lint gate
    v3.0 : Multi-project registry : Switchable project view : GitHub PR status & links
    v3.1 : serve mode + username/password : Tailscale remote access (phone + laptop)
    v3.2 : Live log streaming : All-projects dashboard
    v3.3 : Open-Claude launcher (optional)
    v3.4+ : Public-with-TLS : Audit log : Multi-user
```

## MVP — read-only browsing

`uvx factory-console` in any App-Factory project → browser tab in <5s → list + detail + dep-neighborhood. Single-command launch, 127.0.0.1-only, no writes, wheel on PyPI, CI green.

Build tickets in dependency order:

- [ ] **T01** — Monorepo skeleton (dirs, LICENSE, .gitignore, README stub, .python-version) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T01-monorepo-skeleton.md`
- [ ] **T02** — Python package skeleton + pyproject.toml + factory-console entry point → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T02-python-package-skeleton.md`
- [ ] **T03** — Frontend skeleton (SvelteKit + Tailwind + Vitest + Playwright + openapi-typescript) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T03-frontend-skeleton.md`
- [ ] **T04** — Observability skeleton (logging.py + errors.py base + config.py) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T04-observability-skeleton.md`
- [ ] **T05** — Pre-commit config (ruff + ruff-format + eslint + prettier) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T05-pre-commit-config.md`
- [ ] **T06** — Walking-skeleton FastAPI app + trivial Typer CLI + /api/v1/health → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T06-walking-skeleton-app.md`
- [ ] **T07** — Domain models + TICKET_ID_PATTERN → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T07-domain-models.md`
- [ ] **T08** — Fixture projects (minimal, with_run_state, malformed) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T08-fixture-projects.md`
- [ ] **T09** — Dev + package scripts + Makefile → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T09-dev-and-package-scripts.md`
- [ ] **T10** — FileAdapter Protocol + FakeFileAdapter → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T10-file-adapter-protocol-and-fake.md`
- [ ] **T11** — Upward-walk project discovery → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T11-project-discovery.md`
- [ ] **T12** — tickets.json manifest parser → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T12-manifest-parser.md`
- [ ] **T13** — Ticket .md + front-matter parser → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T13-ticket-md-parser.md`
- [ ] **T14** — Server-side markdown renderer → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T14-markdown-renderer.md`
- [ ] **T15** — Run-state directory prober (read-only) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T15-run-state-prober.md`
- [ ] **T16** — Multi-stage Dockerfile for reproducible builds → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T16-dockerfile.md`
- [ ] **T17** — RealFileAdapter composing all parsers → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T17-real-file-adapter.md`
- [ ] **T18** — CI workflow (matrix lint + tests + build + smoke; conditional e2e) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T18-ci-workflow.md`
- [ ] **T19** — Docs skeleton (architecture.md + usage.md + contributing.md) + README quickstart → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T19-docs-skeleton.md`
- [ ] **T20** — App-factory rewrite (create_app + create_dev_app + DI + error handlers) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T20-app-factory-rewrite.md`
- [ ] **T21** — Project endpoint (GET /api/v1/project) + OpenAPI publish → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T21-project-endpoint.md`
- [ ] **T22** — Tickets list + detail endpoints + TicketService → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T22-tickets-endpoints.md`
- [ ] **T23** — Deps endpoint + DepsService → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T23-deps-endpoint.md`
- [ ] **T24** — Roadmap endpoint + relocated + enriched health handler → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T24-roadmap-and-health-endpoints.md`
- [ ] **T25** — CLI extension: discovery + port + browser + signals + exit codes → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T25-cli-extension.md`
- [ ] **T26** — Release workflow (tag vX.Y.Z → PyPI via OIDC) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T26-release-workflow.md`
- [ ] **T27** — SPA shell + routing + Tailwind base + global error page → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T27-spa-shell.md`
- [ ] **T28** — API client + generated TS types (openapi-typescript) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T28-api-client-and-types.md`
- [ ] **T29** — Shared components: StatusBadge, RunStateBadge, MarkdownBody → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T29-shared-components.md`
- [ ] **T30** — Ticket list route `/` with server-side filter + search → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T30-ticket-list-route.md`
- [ ] **T31** — Ticket detail route `/tickets/[id]` → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T31-ticket-detail-route.md`
- [ ] **T32** — Dep neighborhood route `/tickets/[id]/deps` → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T32-dep-neighborhood-route.md`
- [ ] **T33** — Playwright e2e harness (config + global setup/teardown + happy-path spec) → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T33-playwright-harness.md`
- [ ] **T34** — README screenshots pipeline → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T34-screenshots-pipeline.md`
- [ ] **T35** — Tighten CI: unconditional Playwright + coverage gate at 85% → `/ai-gh-orchestrate-plan docs/planning/tickets/mvp/T35-ci-tightening.md`

## v1 — richer read + navigation

Still read-only, single-process, 127.0.0.1. Adds a dependency-graph view, a roadmap/milestone view, cross-ticket full-text search, and a watchdog-backed live-update channel (the one deliberate extension of the MVP's watcher-free model). Build in dependency order — read layer → API → SPA → e2e:

- [ ] **T36** — Full-text search capability behind the FileAdapter port → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T36-full-text-search-capability.md`
- [ ] **T37** — Dependency graph (DAG) projection behind the FileAdapter port → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T37-dependency-graph-projection.md`
- [ ] **T38** — Structured roadmap parse (milestones + checkbox state) → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T38-structured-roadmap-parse.md`
- [ ] **T39** — FileWatcher port + ChangeEvent + deterministic FakeFileWatcher → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T39-file-watcher-port.md`
- [ ] **T40** — watchdog-backed RealFileWatcher over docs/planning/** + .factory/run-state/** → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T40-watchdog-real-file-watcher.md`
- [ ] **T41** — GET /api/v1/search endpoint + SearchService → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T41-search-endpoint.md`
- [ ] **T42** — GET /api/v1/graph endpoint + GraphService → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T42-graph-endpoint.md`
- [ ] **T43** — Widen GET /api/v1/roadmap to full body + structured milestones → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T43-roadmap-endpoint-widen.md`
- [ ] **T44** — Wire the FileWatcher lifecycle into create_app (inject + lifespan + DI) → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T44-file-watcher-lifecycle.md`
- [ ] **T45** — GET /api/v1/events SSE endpoint + EventsService → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T45-events-sse-endpoint.md`
- [ ] **T46** — Extend the typed api client for the v1 read endpoints → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T46-typed-api-client.md`
- [ ] **T47** — /graph route — Cytoscape dependency DAG (bundled) → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T47-graph-route.md`
- [ ] **T48** — /roadmap route — full body + structured milestones → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T48-roadmap-route.md`
- [ ] **T49** — Global TopBar nav + search box + /search route → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T49-topbar-nav-and-search.md`
- [ ] **T50** — SSE live updates — subscribe to /api/v1/events + refresh view → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T50-sse-live-updates.md`
- [ ] **T51** — Graph-render e2e (DAG renders, run-state colors, click-through) → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T51-graph-e2e.md`
- [ ] **T52** — Search e2e (full-text results, links, empty state) → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T52-search-e2e.md`
- [ ] **T53** — Live-update e2e + dedicated mutable-console harness → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T53-live-update-e2e.md`
- [ ] **T54** — v1 docs + README screenshots refresh → `/ai-gh-orchestrate-plan docs/planning/tickets/v1/T54-docs-screenshots-refresh.md`

## v2 — safe editing of todo tickets

A ticket a factory lane owns remains read-only (matching how `/factory-reconcile-plan` treats them). Build in dependency order — write core → backend → frontend → tests/devops:

- [ ] **T55** — Write-path domain models (TicketDraft, TicketEdit, DiffPreview, WriteResult) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T55-write-domain-models.md`
- [ ] **T56** — RunStateGate — todo-only mutability → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T56-run-state-gate.md`
- [ ] **T57** — Write-render — desired manifest+markdown+roadmap contents → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T57-write-render.md`
- [ ] **T58** — Dry-run diff engine (unified DiffPreview, no writes) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T58-dry-run-diff-engine.md`
- [ ] **T59** — Atomic co-writer (tmp-write + os.replace) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T59-atomic-co-writer.md`
- [ ] **T60** — FileWriter Protocol + FakeFileWriter → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T60-file-writer-port-and-fake.md`
- [ ] **T61** — RealFileWriter — disk-backed FileWriter → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T61-real-file-writer.md`
- [ ] **T62** — Wire the FileWriter port into create_app + CLI (get_file_writer) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T62-file-writer-di-wiring.md`
- [ ] **T63** — WriteService + write error types → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T63-write-service.md`
- [ ] **T64** — Per-session loopback write token (X-Factory-Write-Token) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T64-write-token.md`
- [ ] **T65** — POST/PUT/DELETE /api/v1/tickets with ?dryRun → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T65-ticket-write-endpoints.md`
- [ ] **T66** — Write API client + regenerated types + write-token store & prompt → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T66-write-api-client-and-token-store.md`
- [ ] **T67** — CodeMirror markdown editor + form validation + editability → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T67-markdown-editor-and-validation.md`
- [ ] **T68** — Reusable TicketForm with live validation → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T68-ticket-form.md`
- [ ] **T69** — Diff-preview modal + confirm dialog + unified-diff renderer → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T69-diff-preview-modal.md`
- [ ] **T70** — Gated edit + delete on the ticket detail route → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T70-detail-edit-delete.md`
- [ ] **T71** — Create-ticket route + 'New ticket' affordance → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T71-create-ticket-route.md`
- [ ] **T72** — Property-based write-safety invariant tests (Hypothesis) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T72-write-safety-property-tests.md`
- [ ] **T73** — Integration tests for the write endpoints → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T73-write-endpoint-integration-tests.md`
- [ ] **T74** — Editing e2e (part 1): create/edit/diff-preview/save → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T74-editing-e2e-create-edit.md`
- [ ] **T75** — Editing e2e (part 2): delete-confirm + non-todo banner → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T75-editing-e2e-delete-guardrails.md`
- [ ] **T76** — Signed releases + sigstore attestations → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T76-signed-releases-sigstore.md`
- [ ] **T77** — v2 docs + README refresh → `/ai-gh-orchestrate-plan docs/planning/tickets/v2/T77-v2-docs-refresh.md`

## v2.1 — read what the factory actually writes

A correction milestone. The console was built against a run-state contract that described a directory the factory does not write, and against a vocabulary the factory does not use; v2.1 reads the real source (`.factory/run-state.json`), models the factory's nine real states, splits the write gate so "no run-state source" and "absent from a source that exists" stop being the same answer, and surfaces the factory's other read-only artifacts as run and spend views. Build in dependency order — read layer → gate → API → SPA → devops/docs:

- [ ] **T78** — Read the run-state the factory actually writes, and model the states it actually has → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T78-run-state-json-source.md`
- [ ] **T79** — Ledger reader — typed spend records from `.factory/metrics/ledger.jsonl` → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T79-ledger-reader.md`
- [ ] **T80** — Write gate: split 'no run-state source' from 'absent from a source that exists' → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T80-write-gate-absent-vs-unsourced.md`
- [ ] **T81** — GET /api/v1/runs — what the factory did, per ticket — **superseded** by T88+T89+T90 (four lane runs did not converge; work re-split rather than resumed) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T81-runs-endpoint.md`
- [ ] **T82** — GET /api/v1/spend — what the factory cost → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T82-spend-endpoint.md`
- [ ] **T88** — Runs reader — typed per-ticket result/receipt reads (T81's split, part 1) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T88-runs-reader.md`
- [ ] **T89** — RunRecord domain shape (T81's split, part 2) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T89-run-record-domain.md`
- [ ] **T90** — GET /api/v1/runs endpoint (T81's split, part 3) → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T90-runs-endpoint.md`
- [ ] **T83** — /runs — the run view → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T83-runs-view.md`
- [ ] **T84** — /spend — the cost view → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T84-spend-view.md`
- [ ] **T85** — One lint gate — `make lint` must run what CI runs → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T85-lint-gate-parity.md`
- [ ] **T86** — v2.1 docs — run-state source, runs, spend, and the one lint gate → `/ai-gh-orchestrate-plan docs/planning/tickets/v2.1/T86-v2.1-docs.md`

## v3 — Mission control: hosted multi-project console (epic-level)

Graduates from a local single-project viewer into a long-running, **Tailscale-reachable** service
managing many imported projects. Files stay the source of truth; a tiny SQLite store holds only the
console's own registry + credentials. Staged so each slice is usable on its own:

- **v3.0 — Multi-project read plane (local):** SQLite project registry; add/select project; switchable
  single-project view (today's console + a project dropdown); `GitHubAdapter` for PR status + links.
  Still `127.0.0.1`.
- **v3.1 — Hosting + auth:** `factory-console serve` mode; configurable bind; single username/password
  login (hashed + session cookie); Tailscale deploy doc → reachable from phone + laptop.
- **v3.2 — Live + dashboard:** factory loop/QA **log streaming** (reuses the v1 `FileWatcher` port);
  all-projects **dashboard** homepage (progress %, current milestone, open PRs across projects).
- **v3.3 — (optional) Open-Claude launcher:** per-project button spawning `claude` (tmux) for
  phone-driven work; pending confirmation that Claude Code can attach to a server-started session.
- **v3.4+ — Hardening options:** public-with-TLS deploy path; audit log; multi-user; PR caching.

→ Elaborate later with `/factory-plan-milestone v3` (after v2 is built), continuing the global ticket
IDs — never renumbering existing ones.
