# [T138] GitHubPullService — one entry per manifest ticket, monotone in the source

milestone: v3.0.1 · track: github · depends_on: T132, T131, T10, T89 · provides: `services/github_service.py` — composes the port's project-wide read into one `GitHubPullEntry` per MANIFEST ticket in manifest order, enforcing that a source which could not be read yields the SOURCE's reason for every ticket and `no_pull_request` for none.

## Context

The port answers about a PROJECT; the view asks about TICKETS. Composing the two is where the
monotonicity invariant is actually enforceable and testable, so it gets its own layer rather than
living in the handler — the shape `RunService` established for `/runs`, and the reason the handler
there "adds no logic of its own, deliberately".

**The rule this service exists to hold:** when `source.read` is False, every ticket's entry carries
the source's reason, because `no_pull_request` is a positive claim ("the listing was read and named
nobody") and asserting it from a listing nobody read is precisely the fail-open MONOTONICITY forbids.

It also guarantees an entry EXISTS for every manifest ticket, so a ticket is never silently absent
from the response — missing renders as missing.

## Staged approach

1. CREATE `server/factory_console/services/github_service.py`.
2. `class GitHubPullService` with `__init__(self, adapter: FileAdapter, github: GitHubAdapter)` and
   `list_pull_entries(self, project: Project) -> tuple[GitHubSource, list[GitHubPullEntry]]`.
3. Call `github.list_pull_requests(project)` ONCE, then `adapter.list_tickets(project)` for the id
   list and its order (manifest order, exactly as `/runs`).
4. If `read.source.read` is False: every entry is
   `GitHubPullEntry(ticketId=..., reason=read.source.reason)`. **Assert (and test) that
   `pullsByTicket` is ignored on this branch even if a non-conforming adapter populated it** — the
   service must not be more confident than its input.
5. Else: per ticket, `pullsByTicket.get(id)` → `pullRequest=...`, else `reason="no_pull_request"`.
6. Docstring states the JOIN CONTRACT for consumers: entries are keyed by the same `ticketId`
   `GET /api/v1/runs` uses, so a client joins the two per id; a `pullRequest` here wins over the
   artifact `pr_url` for display, and **ANY reason here — including `no_pull_request` — means fall
   back to the artifact value labelled as artifact-sourced**. This service never reads, copies or
   contradicts `/runs`' artifact data; there is exactly one owner of each source.
7. PRs whose branch names a ticket NOT in the manifest are dropped from the entry list by construction
   (the list is the manifest's) — document that as deliberate, and note that surfacing them is a
   later, separate decision.
8. CREATE `tests/unit/test_github_service.py` against `FakeFileAdapter` + `FakeGitHubAdapter`:
   manifest order preserved; a ticket with a PR; a ticket without; every unreadable-source reason
   propagating to EVERY entry; **a hostile fake seeded with `read=False` AND a populated map still
   producing no `pullRequest`**; an empty manifest producing an empty list with the source block
   intact.

## Critical files

- `server/factory_console/services/github_service.py` (create)
- `tests/unit/test_github_service.py` (create)

## Interface & data

`GitHubPullService(adapter: FileAdapter, github: GitHubAdapter).list_pull_entries(project: Project)
-> tuple[GitHubSource, list[GitHubPullEntry]]`.

Entities by reference: `GitHubSource`, `GitHubPullEntry`, `GitHubPullsRead` (T131), `TicketSummary`
(only `id` is used). Contracts referenced: ARCHITECTURE.md "The resolution invariant" (MONOTONICITY —
the rule this module enforces) and the REST v1 `/runs` entry (the per-ticket-id join key).

DB ops: none; no I/O — pure composition over two ports (the `services/` layer rule from
PROJECT_STRUCTURE's Track ownership). NFR flags: monotonicity enforced and tested; TOTAL — never
raises for a source problem, though a `MalformedManifest` from `list_tickets` propagates exactly as it
does on `/runs`.

## Verification

`python -m pytest tests/unit/test_github_service.py -q`; `make lint`; `python -m pytest -q`.
Still no route; `factory-console <path>` unaffected.
