# [T142] Document the GitHubAdapter port, its REST contract and its operator setup

milestone: v3.0.1 · track: docs · depends_on: T139, T141, T130, T19, T102 · provides: the durable contract for the GitHub read — an ARCHITECTURE.md "GitHubAdapter port (read-only)" section with the reason vocabulary and the `/runs` precedence rule, the REST v1 entry, and the operator setup in `docs/usage.md`.

## Context

`ARCHITECTURE.md` is the file lanes build against, and it currently describes the GitHubAdapter in one
sentence. Everything this track decided that a future lane must not re-decide has to land in the
durable contract, or the next milestone will invent a second answer to the same question:

- the reason vocabulary;
- the account check as a **console-owned pin** — and the recorded finding that the machine's
  dual-account guard is a Claude Code PreToolUse hook with no executable to call, baked to a single
  repo, and reading another tool's uncommitted files, which is why it is NOT copied;
- the two-boundary disclosure narrowing;
- the timeout and byte bounds;
- the `tkt/<id>` join as an unverified convention;
- and above all **the precedence between a `gh`-sourced PR and the artifact-sourced `pr_url`**.

Documentation only: no code changes, so it lands last without blocking anything.

## Staged approach

1. EDIT `docs/planning/ARCHITECTURE.md` — add a `### GitHubAdapter port (read-only)` subsection under
   Contracts, after "FileAdapter port":
   - the one-method Protocol and its TOTAL + monotone contract;
   - the reason vocabulary as a table (reason → what it means → what the operator does);
   - **the account check**: `FACTORY_CONSOLE_GITHUB_ACCOUNT` (with `GH_GUARD_USER` as an optional
     parity override); pin set → must match `gh`'s active account or refuse; pin unset → act as the
     authenticated account and DISCLOSE it; the console never runs `gh auth switch` and never reads
     app-factory's files; and the recorded finding about the hook, so nobody re-proposes shelling out
     to it;
   - the bounds (`FACTORY_CONSOLE_GH_TIMEOUT_SECONDS` default 6 s, 1 MiB stdout cap, `--limit 200`,
     timeout → `timed_out` never empty);
   - the two-boundary disclosure narrowing (`--json` at the source, typed construction at the parse)
     and why `gh` is therefore the **SECOND typed reading path** after the ledger — **update the
     "Other factory artefacts" sentence that currently says the ledger "is the exception and the only
     one"**;
   - the `tkt/<ticketId>` join as an unverified convention declared in one place;
   - what is explicitly NOT in v3.0.1: no caching (v3.4), no GitHub Enterprise, no write, ever.
2. In the same file, add the `GET /api/v1/github/pulls` bullet to "Contracts → REST v1" with the
   response shape, and — **the load-bearing sentence** — the PRECEDENCE rule against the existing
   `/runs` `pr_url`: two sources, one join key, `gh` wins for display when it carries a pull request,
   the artifact value is labelled as artifact-sourced otherwise, `no_pull_request` does NOT erase an
   artifact value, and neither endpoint rewrites the other.
3. In the same file, update the v3 section's GitHubAdapter bullet to point at the new contract
   subsection, and record v3.0.1 as shipped.
4. EDIT `docs/usage.md`: an operator section — what PR status needs (`gh` on PATH, `gh auth login`;
   the pin only if you run more than one account), what each reason means in the UI, that the console
   never changes your `gh` account and never writes to GitHub, and the per-request cost with no cache.
   Reconcile with the v3.0 entries T130 left, so the file reads as one account.
5. Note: `PROJECT_STRUCTURE.md`'s `github_adapter/` tree and ownership row were already added by T132
   and T103 — verify they match what shipped rather than re-adding them. `ROADMAP.md`'s v3.0.1
   checklist is T129's; tick it here only if the milestone-close convention calls for it.

## Critical files

- `docs/planning/ARCHITECTURE.md` (modify — aggregation file)
- `docs/usage.md` (modify — aggregation file)

## Interface & data

N/A — documentation only. It records the contracts the preceding tickets implement (the
`GitHubAdapter` port, `GET /api/v1/github/pulls`, `domain/github.py`'s vocabulary, the
`FACTORY_CONSOLE_GH*` settings) and redefines none of them.

## Verification

`make lint` (pre-commit runs the whitespace/EOF/prettier hooks over docs);
`python -m pytest -q` (unchanged — no code touched).
Review by cross-reading: **every reason listed in the ARCHITECTURE table must exist in
`domain/github.py`'s `Literal`**, and the REST bullet's field names must match `api/v1/github.py`'s
models and the published `/api/v1/openapi.json`.
