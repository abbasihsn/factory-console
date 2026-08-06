# [T136] gh pull-request payload parse + tkt/<id> branch join

milestone: v3.0.1 · track: github · depends_on: T131, T102 · provides: `github_adapter/pulls_parse.py` — the ONE declaration of the fields requested from `gh` (`GH_REQUESTED_FIELDS`), the typed narrowing of its JSON into `PullRequestRef`, and the ONE declaration of the ticket↔branch convention (`tkt/<ticketId>`) that makes the per-ticket join possible.

## Context

`gh`'s output is an unmodelled, externally-written payload, so the disclosure rule binds it. **Unlike
a `.factory/` artifact, `gh` lets the console narrow at the SOURCE as well as at the wire** — it
accepts `--json <fields>` — so this module owns both halves: the requested-field list, and a parser
that constructs nothing but the declared, type-checked fields. Doing the narrowing at parse time means
**no untyped payload ever exists in the process**, which is strictly stronger than the `/runs`
twin-model approach and is why no `dict[str, Any]` will reach a response schema.

This module also owns the JOIN. The factory names lane branches `tkt/<ticketId>` — verified against
this repo's own merge history (`tkt/T101`, `tkt/T102`) — which is what lets a project-wide PR listing
be keyed per ticket id. That convention is an **UNVERIFIED guess about another program**, in the same
sense `DISCLOSED_ARTIFACT_FIELDS` is, so it is declared once, tested, and rendered as the console's own
ignorance when it misses.

## Staged approach

1. CREATE `server/factory_console/github_adapter/pulls_parse.py`.
2. Declare `GH_REQUESTED_FIELDS: tuple[str, ...] = ("number", "url", "state", "isDraft",
   "headRefName", "updatedAt")` with a docstring in the voice of `DISCLOSED_ARTIFACT_FIELDS`: these are
   the only fields ASKED FOR and the only fields that can be constructed — the two a PR view needs
   (status + link) plus what makes them honest (draft, branch, freshness) — and growing the list is a
   deliberate one-place edit.
3. Declare `TICKET_BRANCH_PATTERN = re.compile(r"^tkt/(?P<ticket_id>[A-Za-z0-9_.-]+)$")` and
   `ticket_id_for_branch(head_ref: str) -> str | None`, validating the captured id against
   `domain/ticket.py::TICKET_ID_PATTERN` rather than re-spelling the rule. Docstring: this is the
   observed factory convention, it is unverified, **part branches (`factory/<milestone>-part-N`)
   deliberately do NOT match**, and a miss must render as "no PR under any branch name this console
   recognises".
4. `parse_pull_requests(payload: bytes) -> list[PullRequestRef] | GitHubSourceReason`: `json.loads` →
   `unparseable` on failure or when the document is not a LIST. Each element must be a dict; read only
   the declared keys; type-check each (`number: int` and not `bool`, `url: str`, `isDraft: bool`,
   `headRefName: str`, `updatedAt` ISO-8601 parseable, `state` normalized `lower()` into
   `PullRequestState` with anything unrecognised becoming `unknown` — **never `open`**).
   **A non-conforming ELEMENT is SKIPPED, never coerced** (`str(value)` on something the console cannot
   model is the disclosure this refuses), and the skip is logged as a COUNT only, never as content. A
   whole-document failure is `unparseable`; a partially-skipped list is still a successful read.
5. `index_by_ticket(refs: Iterable[PullRequestRef]) -> dict[str, PullRequestRef]`: keep only refs whose
   head branch yields a ticket id; on a collision keep the most recent `updatedAt`, tie-broken by the
   higher `number`. Deterministic and documented.
6. CREATE `tests/unit/test_github_pulls_parse.py`: a realistic multi-PR payload; a non-array document;
   invalid JSON; an element missing a field; an element with a non-string url; a
   non-`https://github.com/` url; an unrecognised state → `unknown`; a `factory/v3.0-part-1` branch
   producing no key; a `tkt/` collision resolving by `updatedAt`; and **a test asserting
   `GH_REQUESTED_FIELDS` matches exactly the fields `PullRequestRef` declares**, so the two lists cannot
   drift — the guard `projected-fields.test.ts` performs on the frontend side.

## Critical files

- `server/factory_console/github_adapter/pulls_parse.py` (create)
- `tests/unit/test_github_pulls_parse.py` (create)

## Interface & data

`parse_pull_requests(payload: bytes) -> list[PullRequestRef] | GitHubSourceReason`;
`index_by_ticket(refs) -> dict[str, PullRequestRef]`; `ticket_id_for_branch(head_ref) -> str | None`.
Constants: `GH_REQUESTED_FIELDS`, `TICKET_BRANCH_PATTERN`.

Contracts referenced: ARCHITECTURE.md "Other factory artefacts (read-only)" → the disclosure rule
(narrowed HERE at the source and at construction, so no untyped payload exists);
`domain/ticket.py::TICKET_ID_PATTERN` (reused, not restated); `PullRequestRef` / `PullRequestState`
from T131. Upstream shape parsed: the JSON array
`gh pr list --json number,url,state,isDraft,headRefName,updatedAt` emits.

DB ops: none; no I/O. NFR flags: disclosure allowlist declared in one place and tested; never coerce
an unmodelled value; payload content never logged.

## Verification

`python -m pytest tests/unit/test_github_pulls_parse.py -q`; `make lint`; `python -m pytest -q`.
No wiring changes; `factory-console <path>` unaffected.
