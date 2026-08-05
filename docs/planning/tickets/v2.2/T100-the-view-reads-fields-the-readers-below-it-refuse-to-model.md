# [T100] `/runs` reads two fields its own readers declare unmodelled

milestone: v2.2 · track: frontend · depends_on: T90 · source: Incremental Integration Audit run 3 (2026-08-05), lens `incompatible-api-schema-event`, tickets T83,T86,T88

## Context

Three layers of this stack say, in writing, that the console does not know what is inside a lane
result. The fourth reads two named fields out of it and renders them as columns.

- `tests/fixtures/runs/README.md:7-18` — *"ILLUSTRATIVE EXAMPLE SHAPES, not verbatim captures … do
  not read them as the factory's contract, and do not build a schema from them."*
- `server/factory_console/domain/runs.py:95-105` — *"Do not 'improve' this into a typed schema —
  here or there — until a real captured artifact exists to verify against."*
- `docs/planning/ARCHITECTURE.md:279` — *"it models no field inside them beyond what it has
  verified."*
- `frontend/src/routes/runs/+page.svelte:142` reads `pr_url`; `:249` reads `status`.

Those two field names came from the fixtures the README says are not the contract. The view is
careful about it — `:279` and `:310` render the console's ignorance honestly, as *"names no PR url"*
and *"no status under any key this console recognises"* — so nothing crashes and nothing lies.

**What it costs is the whole point of the view.** Against a real factory project whose result
artifacts spell those keys differently, every ticket with a result renders `—` in both the PR and
Outcome columns, and the page reports "we found your results and understood none of them" in the
gentlest possible language. The console would be working exactly as designed and telling the operator
nothing. Nobody has yet run it against a captured artifact from a project other than this one, so
whether the two names are right is **untested**, not merely unverified.

**The contradiction is the deliverable, not the field names.** Either the console models these fields
— in which case the three notices above are stale and the schema is verified against a real capture
— or it does not, in which case a view must not depend on two of them. The current state is the
worst of both: a dependency with no contract, and a written policy saying there is no dependency.

**Found by the audit, not by a review.** T88 declared the artifact unmodelled, T86 wrote the notice
into ARCHITECTURE.md, T83 built the columns. Each is defensible alone.

**Non-blocking.** Run 3 returned `blocking: 0`; v2.1's Version is not gated on this.

## Acceptance criteria

1. Pick one, and make the codebase say only that one:
   - **model it** — a captured result artifact from a real factory run is committed as a fixture,
     `pr_url` and `status` are verified against it, the type carries them, and the three notices are
     rewritten to describe what IS modelled; or
   - **don't** — the two columns stop depending on unverified key names, by whatever means (a
     declared, documented projection the server performs; or dropping the columns).
2. Whichever is chosen, `tests/fixtures/runs/README.md`, `domain/runs.py`'s comment and
   `ARCHITECTURE.md:279` agree with the code and with each other. A grep for "do not build a schema
   from them" must not sit above code that built one.
3. A test that fails if a future field is read from the artifact payload without appearing in
   whatever the answer to criterion 1 established as the contract.
4. The all-absent and unrecognised-shape renderings T83 built are preserved — this Ticket is about
   what the view may depend on, not about weakening how honestly it reports ignorance.
