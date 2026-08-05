# [T102] Two endpoints, added in one version, take opposite positions on disclosing artifact content

milestone: v2.2 · track: backend · depends_on: T90 · source: Incremental Integration Audit run 3 (2026-08-05), lens `architecture-drift`, tickets T82,T88,T90

## Context

`/spend` and `/runs` both read files the factory writes into `.factory/`. They disagree about what a
read-only endpoint may hand back, and they were built in the same version.

**`/spend` treats artifact content as sensitive.** `ledger.py:84-92` redacts `session_id` out of an
excerpt before it can leave the module, and `domain/spend.py:187-198` then declines to project that
excerpt at all — *"would widen what this read-only endpoint can disclose for no view that needs it."*
Even an 80-character fragment is refused on the grounds that nothing asked for it.

**`/runs` publishes it whole.** `domain/runs.py:110-112` carries the artifact as an untyped
`dict[str, Any]`, `domain/run_record.py:59-61` carries it verbatim, and `api/v1/runs.py:61`
serialises every key of both per-ticket artifacts — for a view that consumes exactly two of them
(`frontend/src/routes/runs/+page.svelte:142` and `:249`).

By these same modules' own account (`tests/fixtures/runs/README.md:7-18`) **the console does not
know what else those artifacts contain.** So the disagreement is not a considered trade-off between
two known payloads; one endpoint refuses to disclose a redacted fragment of a file it understands,
and the other forwards the entirety of files it explicitly does not.

Lane results and receipts are written by the factory, and the factory's own metrics carry session
ids, model names, token counts and cost. Whether any of that reaches a result artifact is not
knowable from this repository — which is the argument, not a mitigation: `/spend` redacts a session
id it *can* see, and `/runs` forwards keys it cannot.

**What this Ticket is asking for is a rule, not a patch.** One sentence in `ARCHITECTURE.md` that
decides whether a read-only endpoint may forward an unmodelled factory artifact verbatim, and both
endpoints obeying it. Absent that, the next endpoint to read an artifact picks whichever neighbour it
happens to copy — which is exactly how these two came to differ.

**Found by the audit, not by a review.** It takes T82, T88 and T90 together; each endpoint is
internally consistent.

**Non-blocking.** Run 3 returned `blocking: 0`; v2.1's Version is not gated on this.

## Acceptance criteria

1. `ARCHITECTURE.md` states the rule: whether a read-only endpoint may serialise an unmodelled
   factory-written artifact verbatim, and if not, what it may serve instead (a declared projection, a
   modelled subset). One sentence a future endpoint author can apply without reading either
   implementation.
2. `/spend` and `/runs` both obey it. If the rule permits verbatim forwarding, `/spend`'s refusal at
   `domain/spend.py:187-198` is revisited and its comment corrected; if it does not, `/runs` serves a
   declared subset. **Either outcome is acceptable; the two disagreeing is not.**
3. A test that fails if a handler serialises an artifact payload the rule does not permit — attached
   to the rule, not to today's two endpoints, so a third one inherits the check.
4. Coordinate with T100: that Ticket decides what `/runs` may *depend on*, this one decides what it
   may *disclose*. They can be built in either order but must not settle on contradictory answers —
   whichever lands second reads the first's decision.
