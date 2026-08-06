# [T128] DevOps for the console's first writable state: Dockerfile, CI, and the packaging note

milestone: v3.0 · track: infra-devops · depends_on: T119, T120 · provides: the Docker image and CI reckon with the fact that installing the console now creates persistent state in the user's home directory — or record, deliberately, that the image stays single-project.

## Context

v3.0 gives the console its first writable state outside a target project, and nothing in the
milestone so far touches the delivery surface. `sdlc_scope` marks infra/devops in-scope and every
prior milestone carried at least one such ticket (T35, T76, T85); skipping it here would leave three
concrete gaps:

- **The Dockerfile.** The multi-stage image has no writable `~/.factory-console`, no declared volume,
  and no documented `FACTORY_CONSOLE_DB_PATH`. A container that cannot create its registry either
  crashes on first registry use or silently loses the registry on every restart. Either outcome
  should be a decision, not an accident.
- **CI.** The e2e job must export an isolated `FACTORY_CONSOLE_DB_PATH` (T120 does it in the harness;
  CI should assert it rather than trust it), and the runner's home must come back clean.
- **Release/packaging.** `uvx factory-console` now creates persistent state in `$HOME`. That is a
  user-visible change to what installing this tool does, and it belongs in the docs and the release
  notes rather than being discovered.

**A legitimate outcome of this ticket is "the Docker image stays single-project and registry-less."**
The image is explicitly not the primary distribution. If that is the call, record it in the ticket
AND in ARCHITECTURE.md's DevOps section, rather than leaving the container's behaviour unstated.

## Staged approach

1. `Dockerfile`: decide and implement one of —
   (a) declare a writable location via `ENV FACTORY_CONSOLE_DB_PATH=...` under a directory the runtime
   user owns, plus a `VOLUME` so the registry survives a restart; or
   (b) leave the image registry-less and document that it serves the single project it is pointed at.
   Whichever is chosen, write the reasoning into the Dockerfile as a comment and into
   ARCHITECTURE.md's DevOps section.
2. `.github/workflows/ci.yml`: export an isolated `FACTORY_CONSOLE_DB_PATH` for the e2e and pytest
   jobs, and add a step asserting `~/.factory-console/` does not exist after the suite runs — the CI
   half of T120's guarantee, so a future change that reintroduces the leak fails a check rather than
   quietly polluting a runner.
3. `docs/usage.md` (or the README's install section, whichever already carries install notes): one
   line stating that the console keeps its own registry at `~/.factory-console/console.db`, that it is
   created lazily on first registry use, that it holds no ticket data, and how to relocate or delete
   it.
4. Confirm `scripts/package.sh` and the release workflow need no change (the db is runtime state, not
   packaged data) and say so in the ticket rather than leaving it unexamined.

## Critical files

- `Dockerfile` (modify)
- `.github/workflows/ci.yml` (modify)
- `docs/usage.md` (modify — aggregation file)
- `docs/planning/ARCHITECTURE.md` (modify — DevOps section only; aggregation file)

## Interface & data

N/A — delivery surface. It records, by reference: `FACTORY_CONSOLE_DB_PATH` and the default
`~/.factory-console/console.db` (T104), the lazy-creation property (T108), and the harness isolation
(T120).

DB ops: none. NFR flags: file-permission expectations carried into the container (0700 dir / 0600
file per T105); CI hermeticity — no test may write to the runner's real home; no change to the
loopback bind or the release/OIDC path.

## Verification

Build the image and run it against a mounted fixture project: `docker build .` then run with the
fixture mounted, and confirm the console serves it and that the registry behaves as the chosen option
documents. Push a branch and confirm CI is green with the new assertion, then locally verify the
assertion actually fails when isolation is removed (flip it off once, see red, restore). `make lint`.
