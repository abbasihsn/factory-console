# [T131] Domain result types for the GitHub PR read (found/read/reason vocabulary)

milestone: v3.0.1 · track: github · depends_on: T07, T88, T90 · provides: `domain/github.py` — the named-missing vocabulary and the typed result shapes every later GitHub ticket returns: a project-level `GitHubSource {found, read, reason, repo, account}` and a per-ticket `GitHubPullEntry {ticketId, pullRequest | reason}`.

## Context

The GitHubAdapter is the console's first source outside the filesystem, and it can fail in a dozen
distinguishable ways the existing `.factory/` vocabulary does not name (`gh` absent, no pin, wrong
account, no remote, non-GitHub remote, invisible repo, rate-limited, timed out). The console's
standing discipline is that a missing source renders as NAMED-MISSING — never as zero, never as empty
— so the reason vocabulary has to exist and be pinned by tests BEFORE any code can invoke `gh`.

This ticket lands the types only: no I/O, no subprocess, nothing wired.

It also fixes, in one place, **the relationship between a `gh`-sourced PR and the artifact-sourced
`pr_url` that `GET /api/v1/runs` already surfaces**, so the two can never become two competing
answers to the same question — the defect class the whole v2.2 milestone was filed against.

## Staged approach

1. CREATE `server/factory_console/domain/github.py`.
2. Declare `GitHubSourceReason = Literal["gh_absent", "gh_unauthenticated", "gh_account_mismatch",
   "git_absent", "not_a_git_repo", "no_remote", "not_github", "repo_not_visible", "rate_limited",
   "timed_out", "too_large", "unparseable", "unreadable"]`, each member documented with what it MEANS
   and what an operator does about it (the `ArtifactSkipReason` docstring is the model).
3. Declare `GitHubEntryReason = GitHubSourceReason | Literal["no_pull_request"]`.
   **`no_pull_request` is the ONLY reason a successful read can produce**, and it means "the listing
   was read and no PR's head branch matched this ticket under any convention this console
   recognises" — i.e. the console's own ignorance, not a fact about GitHub.
4. Declare `PullRequestState = Literal["open", "closed", "merged", "unknown"]`. `unknown` is the
   monotone answer for a state string this console cannot name, and **MUST NOT be folded into
   `open`**.
5. Declare `PullRequestRef` (frozen, `extra="forbid"`): `number: int`, `url: str`,
   `state: PullRequestState`, `isDraft: bool`, `headRefName: str`, `updatedAt: datetime`.
   Field-validate `url` to start with `https://github.com/` — a link the console cannot attribute is
   not disclosed.
6. Declare `GitHubSource` (frozen, `extra="forbid"`): `found: bool`, `read: bool = False`,
   `reason: GitHubSourceReason | None = None`, `repo: str | None = None`, `account: str | None =
   None`. `account` is the account the query RAN AS, disclosed so an operator can tell which identity
   answered. Add a `model_validator(mode="after")` enforcing `read is True iff reason is None`, and
   `found is False implies read is False`.
7. Declare `GitHubPullEntry` (frozen, `extra="forbid"`): `ticketId: TicketId`,
   `pullRequest: PullRequestRef | None`, `reason: GitHubEntryReason | None`, with the same
   exactly-one-of validator `ArtifactRead._exactly_one_outcome` uses, and the same reasoning quoted.
8. Declare `GitHubPullsRead` (the PORT's return, deliberately not a wire type):
   `source: GitHubSource`, `pullsByTicket: dict[str, PullRequestRef]` — **empty whenever
   `source.read` is False**.
9. Write the module docstring's PRECEDENCE paragraph: a `gh`-sourced PR **complements** `/runs`'
   artifact `pr_url` and is JOINED per ticket id; it never supersedes the artifact SOURCE. A
   `pullRequest` wins for display; **ANY reason — including `no_pull_request` — falls back to the
   artifact value labelled as artifact-sourced.** State why `no_pull_request` must not erase an
   artifact `pr_url`: the branch join is an unverified convention, so a miss is our ignorance.
10. Do NOT add these names to `domain/__init__.py` — consumers import `factory_console.domain.github`
    by full path, the discipline `domain/runs.py` follows. This keeps every sibling ticket in this
    track off a shared aggregation file.
11. CREATE `tests/unit/test_domain_github.py`: both validators reject both impossible combinations;
    `found=False, read=True` is unconstructible; a non-`https://github.com/` url is rejected; an empty
    `pullsByTicket` is a legal read.

## Critical files

- `server/factory_console/domain/github.py` (create)
- `tests/unit/test_domain_github.py` (create)

## Interface & data

Entities implemented: `GitHubSourceReason`, `GitHubEntryReason`, `PullRequestState`,
`PullRequestRef`, `GitHubSource`, `GitHubPullEntry`, `GitHubPullsRead`.

Contracts by reference: ARCHITECTURE.md "Other factory artefacts (read-only)"
(missing-must-render-as-missing; the disclosure rule), "The resolution invariant" (MONOTONICITY), and
the v3 section's GitHubAdapter bullet. Reuses `domain/ticket.py::TicketId` — does not redefine the
ticket-id pattern. Precedents mirrored, not redefined: `domain/runs.py::ArtifactRead`
(exactly-one-of), `domain/spend.py::SourceInfo` (found/read).

DB ops: none. No I/O of any kind. NFR flags: these are the types the disclosure and monotonicity NFRs
are later enforced on.

## Verification

`python -m pytest tests/unit/test_domain_github.py -q`; `make lint`; then `python -m pytest -q` to
confirm nothing else moved (no module imports this yet). `factory-console <path>` is unaffected — no
route, wiring or import-graph changes.
