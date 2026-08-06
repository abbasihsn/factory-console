# [T132] GitHubAdapter port + FakeGitHubAdapter

milestone: v3.0.1 · track: github · depends_on: T131, T10, T88, T89 · provides: the `GitHubAdapter` Protocol (one TOTAL method) and a deterministic in-memory `FakeGitHubAdapter`, so the service, the endpoint and every test can be built with no `gh`, no subprocess and no network — plus the `github_adapter/` tree and ownership row in PROJECT_STRUCTURE.md.

## Context

The port comes before any implementation so the consumer side (service, endpoint, integration tests)
can be built in parallel with the `gh` plumbing, and so the TOTALITY contract — a conforming
implementation never raises for a source-level problem — is stated where implementers read it rather
than discovered later.

It is a SEPARATE small Protocol rather than new methods on `FileAdapter`, for exactly the reason
`run_artifacts.py` gives: this repo has twice added a capability the eight-method read port does not
carry (`FileWriter`, `FileWatcher`) without forcing every implementer of that list to grow methods for
a concern it does not have.

The fake is also the seam that guarantees tests never touch the network, so it lands with the port
and not after it.

This ticket also documents the package in `PROJECT_STRUCTURE.md` **at the start of the track, not the
end** — the store track's T103 set that precedent, and deferring it to the last ticket of the
milestone would leave five modules undocumented for nine consecutive ticket boundaries.

## Staged approach

1. CREATE `server/factory_console/github_adapter/__init__.py` — **DOCSTRING ONLY**. State that the
   package is deliberately re-export-free (no `from .x import ...`, no `__all__`): consumers import
   submodules by full path, so sibling tickets adding modules here never edit a shared file. Do not
   add re-exports in any later ticket.
2. CREATE `server/factory_console/github_adapter/protocol.py`: `@runtime_checkable class
   GitHubAdapter(Protocol)` with the single method
   `def list_pull_requests(self, project: Project) -> GitHubPullsRead: ...`. The docstring carries the
   contract:
   - **read-only** — it never mutates GitHub, in any version, and never runs `gh auth switch`;
   - **TOTAL** — a missing `gh`, a wrong account, a non-git or non-GitHub project, a rate limit, a
     timeout, an oversized or unparseable payload all arrive as a NAMED `GitHubSource.reason`, never
     as an exception;
   - **monotone** — a degraded read may never answer more confidently than a better-understood one;
     in particular `pullsByTicket` MUST be empty whenever `source.read` is False, so nobody can infer
     "no PR" from "could not ask".
   One method, taking the resolved `Project` first, mirroring `FileAdapter` / `RunArtifactReader`.
3. CREATE `server/factory_console/github_adapter/fake.py`: `FakeGitHubAdapter`, satisfying the
   Protocol structurally, seeded with `source: GitHubSource | None = None` and
   `pulls_by_ticket: dict[str, PullRequestRef] | None = None`, plus an optional per-project-root map
   so a multi-project test can seed different answers per registered project. **Default with no seed =
   a fully successful, EMPTY read** (`found=True, read=True, reason=None, pullsByTicket={}`) — the
   fake's default must be an answer the real adapter can actually give. Add a convenience constructor
   `FakeGitHubAdapter.unavailable(reason)` returning `found=False, read=False`, so a degradation test
   does not hand-build a `GitHubSource` and risk building an impossible one. Touches no filesystem, no
   subprocess, no clock.
4. Modify `docs/planning/PROJECT_STRUCTURE.md`: add the `server/factory_console/github_adapter/` tree
   entry (listing the modules T133–T137 will add) and confirm the `github` ownership row T103 created
   covers `domain/github.py` and `services/github_service.py`.
5. CREATE `tests/unit/test_fake_github_adapter.py`: `isinstance(fake, GitHubAdapter)` holds; the
   default answer is the empty successful read; `unavailable()` yields `read=False` with an empty
   `pullsByTicket`; a seeded map round-trips; the fake never raises for any input.

## Critical files

- `server/factory_console/github_adapter/__init__.py` (create — docstring only, no re-exports)
- `server/factory_console/github_adapter/protocol.py` (create)
- `server/factory_console/github_adapter/fake.py` (create)
- `docs/planning/PROJECT_STRUCTURE.md` (modify — aggregation file)
- `tests/unit/test_fake_github_adapter.py` (create)

## Interface & data

Port method: `list_pull_requests(self, project: Project) -> GitHubPullsRead`.

Entities consumed by reference: `domain/project.py::Project`, and `GitHubPullsRead` / `GitHubSource` /
`PullRequestRef` from T131 — none redefined. Contracts referenced: ARCHITECTURE.md "FileAdapter port"
(the port shape and `@runtime_checkable` rationale) and the v3 GitHubAdapter bullet.

DB ops: none. NFR flags: read-only port (never mutates GitHub); TOTAL (no exceptions for source
failures); the fake is the no-network test seam.

## Verification

`python -m pytest tests/unit/test_fake_github_adapter.py tests/unit/test_domain_github.py -q`;
`make lint`. Confirm the isolation property holds — importing `factory_console.app` must not pull in
any concrete GitHub module (nothing imports one yet, and T139 keeps it lazy).
`factory-console <path>` unaffected.
