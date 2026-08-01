# [T89] The run record: one typed record per manifest ticket

milestone: v2.1 · track: backend · depends_on: T88 · provides: `domain/run_record.py` and `services/run_service.py` — one run record per manifest ticket, composed from the readers, with every absent source named rather than silently blank.

## Context

Second of three replacing **T81** (see T88 for why it was split). This ticket owns the **composition**: turning the reader layer's typed results into one record per ticket.

**The hard requirement, and T81's own words for it:** *"every absent source named"*. A run record whose fields are blank because an artifact was missing must say which artifact was missing. A blank field that could mean either "the factory recorded nothing" or "we did not look" is the defect this whole milestone exists to remove — and DL-058 is the cost of getting it wrong at the factory's own layer.

## Staged approach

1. `domain/run_record.py`: a frozen, `extra="forbid"` model like every other domain model, carrying the composed view plus an explicit record of which sources were absent or unreadable.
2. `services/run_service.py`: compose one record per **manifest** ticket — the manifest is the ticket list, the artifacts are evidence about it. A ticket with no artifacts at all is still a record, with every source named absent.
3. Never raise for a missing artifact. A source-level problem is reported in the record, not as a request failure.

## Critical files

- `server/factory_console/domain/run_record.py`
- `server/factory_console/services/run_service.py`

## Interface & data

New domain model and service. Consumes T88's readers; exposes no HTTP surface — that is T90. No schema change beyond the new model.

## Verification

Pytest: a ticket with every artifact present → fully populated record; with none → a record naming **each** absent source, asserted per source rather than as a count; with a malformed artifact → the record distinguishes malformed from absent; a manifest ticket the factory never ran → present in the output, not omitted. Assert the **absence is named**, which is the converse assertion this ticket turns on. `make lint`, `pytest` green.
