"""One composed record per MANIFEST ticket: what the factory recorded, or why not.

T88 gave the console two per-ticket readers — ``.factory/results/<id>.json`` and
``.factory/receipts/<id>.json`` — each answering with an
:class:`~factory_console.domain.runs.ArtifactRead`. This module composes them:
one :class:`RunRecord` per ticket in the MANIFEST, which is the list, with the
artifacts as evidence ABOUT that list rather than the list itself. A ticket the
factory has never run is therefore still a record — with both sources named
``absent`` — and not an omission, because "the factory has not got to this
ticket" is an answer the console must be able to render, and a record that is
simply missing from the output cannot say it.

``.factory/last-stop.json`` is deliberately NOT here. It carries no ticket id: it
is one artifact per PROJECT saying why the last run stopped, so attaching it to a
per-ticket record would duplicate one project-wide fact across every ticket and
invite a reader to interpret it as being about the ticket it is attached to.
Whichever ticket surfaces it owns where it belongs.

These are the console's OWN composed types — nothing on disk is parsed into
them — so they keep the house ``frozen`` / ``extra="forbid"``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from factory_console.domain.runs import ArtifactRead
from factory_console.domain.ticket import TicketId


class RunRecord(BaseModel):
    """The factory's two per-ticket artifacts for ONE manifest ticket, reasons and all.

    Both artifacts are carried as the reader's own :class:`ArtifactRead`
    VERBATIM, and that is the whole point of this type. Each ``ArtifactRead``
    already pairs "here is the object" with, on the other branch, exactly WHY
    there is none — ``absent`` (the factory never wrote it), ``unreadable``
    (nothing was read: the bytes would not come, or the path could not be proven
    safe to read at all, so existence is NOT established — see
    :data:`~factory_console.domain.runs.ArtifactSkipReason`, which forbids
    inferring it), ``unparseable``
    (it answered, unintelligibly) or ``too_large``. Collapsing the pair into a
    summary — a ``hasResult`` boolean, a count of missing sources, a bare
    ``None`` — would put back precisely the ambiguity T88 built the type to
    remove: a blank field that means either "nothing was recorded" or "we did not
    look". Every absent source must be NAMED, per source. Do not flatten these.

    The two sources are independent: a lane can have written a result and no
    receipt, or a corrupt receipt beside a clean result, so each field carries its
    own reason and neither may be inferred from the other.

    ``ticketId`` comes from the manifest, not from the artifacts — the artifacts
    are keyed BY it — and is :data:`~factory_console.domain.ticket.TicketId`
    constrained like every other id on the domain surface.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticketId: TicketId
    result: ArtifactRead
    receipt: ArtifactRead
