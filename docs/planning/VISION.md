# Vision — Factory Console

## One-line

A standalone local console that points at any App Factory–generated project directory
and lets you browse its tickets — status, title, description, and dependencies —
read-only first, with editing (respecting factory run-state immutability) as a later
milestone.

## Problem

App Factory projects live on disk as a folder tree of ticket `.md` files, a
`docs/planning/tickets.json` manifest, a `ROADMAP.md`, and — once the factory has run —
a run-state directory that marks which tickets are immutable (in-flight, ready, merged).
Today, understanding "what's in this project, where does it stand, and what depends on
what" means opening those files by hand or grepping the manifest. There's no dedicated
viewer that presents a factory project as a coherent, browsable whole.

## Target users

- **Developers using the App Factory** — the primary user. Needs a fast local view of
  their project's tickets and dependencies without switching between JSON, markdown,
  and factory run-state directories.
- **Reviewers / collaborators** — pulling a factory-generated repo and wanting to
  orient themselves before running or reviewing lanes.
- **Future: self** editing tickets before the factory has claimed them, without
  touching in-flight/ready/merged work.

## Value proposition

One command from any factory project directory: `factory-console` opens a local browser
UI that shows every ticket at a glance (id, status, title, track), lets you drill into
a ticket's full detail (context, approach, deps, provides, files), and shows the
dependency neighborhood — all pulled live from the project's own files. Zero setup,
zero server infra, zero coupling to a cloud service; it's a local viewer over local
files.

## MVP scope (v0, read-only)

- **Target discovery:** `factory-console [path]` — arg wins, else walk up from cwd
  looking for `docs/planning/tickets.json`. Same ergonomics as git.
- **Ticket list:** id / status / title / track, filterable + searchable.
- **Ticket detail:** full ticket `.md` rendered, plus resolved `depends_on` /
  `provides` / factory run-state (todo / in-flight / ready / merged / etc.), plus a
  link back to the ticket file on disk.
- **Dep view:** for the selected ticket, the direct deps and dependents as clickable
  lists (no rendered graph yet — that's v1+).
- **Live-ish reads:** files re-read on refresh; no caching layer, no background
  watcher, no writes.
- **Local-only server:** binds to 127.0.0.1, no auth (local trust boundary), single
  process the user Ctrl-C's when done.

## Later versions (epic-level roadmap, elaborated just-in-time)

- **v1 — richer read + navigation:** rendered dependency graph, roadmap/milestone
  view, cross-ticket search, file-watcher for live updates.
- **v2 — safe editing:** create/edit `todo` tickets in the UI, respecting factory
  run-state immutability (in-flight/ready/merged tickets are read-only, matching how
  `/factory-reconcile-plan` treats them); manifest + roadmap kept in sync; single-commit
  writes behind a confirm.
- **v3 — hosted multi-project control plane ("mission control"):** the console graduates from a
  local single-project viewer into a long-running, Tailscale-reachable service that manages *many*
  imported projects — a switchable single-project view first, an all-projects dashboard later — with
  live GitHub PR status and an optional per-project launcher for phone-driven work. Files stay the
  source of truth; the console gains a tiny store for its *own* state only (project registry +
  credentials — never ticket data).

## v3 charter shift (deliberate expansion of the v0 non-goals)

v0 was scoped local / single / read-only **on purpose**. v3 revisits several of the v0 non-goals
below **deliberately** — this table is the record that they were reversed by design, not by accident:

| v0 non-goal | v3 |
|---|---|
| no remote access | **Tailscale-first** (public-with-TLS a later deploy-time option) |
| single project | **multi-project registry** + switchable selection |
| no authentication | **single username/password** (single user for now) |
| not a long-running daemon | long-running **`serve`** mode |
| no GitHub integration | **read-only** PR status + links |

Tickets / run-state / roadmap remain read live from each project's files; the console never becomes
their source of truth.

## Success criteria

- From `cd` into a factory-generated project → `factory-console` → useful ticket
  overview in the browser in **under 5 seconds**.
- All information visible in the UI is derivable from files in the project (no hidden
  state); closing the browser and re-running gives the same view.
- On a project with 100 tickets the list and detail load with no perceptible lag on a
  developer laptop.
- Zero writes to the project directory in v0 (verifiable — no code path opens files
  for write).

## Constraints & non-functional requirements

- **Standalone / local:** no cloud service, no login, no external API dependencies.
  Runs entirely on the user's machine against local files.
- **Cross-platform:** macOS + Linux at minimum; Windows a stretch goal.
- **Single-command launch:** one binary or one `npx`/`pipx`/`uvx`-style invocation.
- **Safe by default:** binds to 127.0.0.1 only; no writes in v0; editing in v2 must
  refuse to touch any ticket whose factory run-state is not `todo`.
- **Small footprint:** installable in seconds, disposable; not a long-running daemon.
- **Compatible with factory data contracts:** consumes `docs/planning/tickets.json`
  and ticket `.md` files as written by `/factory-plan-project`,
  `/factory-plan-milestone`, and `/factory-reconcile-plan`; consumes the factory
  run-state directory as written by `bin/factory-pm` / `bin/factory-lead`. Never
  mutates run-state.
- **No coupling to the factory runtime:** the console reads factory-produced files
  but does not import or call the factory. It works against any directory shaped
  like a factory project, even one produced offline.

## Non-goals (v0)

- No editing, no writing, no ticket creation.
- No rendered DAG (deferred to v1).
- No integration with git operations, PRs, or GitHub.
- No authentication, multi-user, or remote access.
- No embedding of the factory runtime.
