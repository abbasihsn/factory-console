"""The write-path :class:`FileWriter` port — the mirror of :class:`FileAdapter`.

Where :class:`~factory_console.file_adapter.protocol.FileAdapter` is the read-only
seam the query handlers depend on, :class:`FileWriter` is the write-side seam the
v2 ``POST``/``PUT``/``DELETE`` endpoints depend on (wired via ``FastAPI.Depends()``)
so a handler plans and applies a ticket write without ever calling ``open()``
itself. Two implementations satisfy it structurally — a filesystem-backed writer
and the in-memory :class:`~factory_console.file_adapter.fake_writer.FakeFileWriter`
used by tests — exactly as ``FakeFileAdapter`` mirrors the read port.

The six methods pair up: each ``preview_*`` computes a pure, side-effect-free
:class:`~factory_console.domain.write.DiffPreview` of what a create/edit/delete
WOULD change, while its apply sibling enforces the run-state write gate, performs
the write, and returns a :class:`~factory_console.domain.write.WriteResult`.
Every method takes the resolved :class:`~factory_console.domain.project.Project`
first, mirroring ``FileAdapter``'s shape.

Edit and delete do NOT share one gate, and this port is where that must be stated,
because it is the contract every implementation is written against: ``edit_ticket``
authorizes over
:data:`~factory_console.file_adapter.write_gate.MUTABLE_STATES` (``todo``/
``unknown``), while ``delete_ticket`` authorizes over
:data:`~factory_console.file_adapter.write_gate.DELETABLE_STATES`, which ADDITIONALLY
permits :attr:`~factory_console.domain.run_state.RunState.absent` — otherwise the
ungated ``create_ticket`` could mint a ticket no implementation would ever delete
(T80 amendment, gap 2). An implementation that gates delete like edit is not a
conforming ``FileWriter``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from factory_console.domain import Project
from factory_console.domain.write import DiffPreview, TicketDraft, TicketEdit, WriteResult


@runtime_checkable
class FileWriter(Protocol):
    """Write seam between the write endpoints and filesystem I/O.

    The mirror of :class:`~factory_console.file_adapter.protocol.FileAdapter` for
    the write path: every method takes the resolved
    :class:`~factory_console.domain.project.Project` for the request first. The
    ``preview_*`` methods are pure — they compute a
    :class:`~factory_console.domain.write.DiffPreview` and mutate nothing — while
    the two GATED apply methods enforce their run-state gate before writing
    (``MUTABLE_STATES`` for edit, the wider ``DELETABLE_STATES`` for delete — see the
    module docstring) and return a :class:`~factory_console.domain.write.WriteResult`.
    :meth:`create_ticket` is the third apply method and is deliberately NOT gated;
    the whole reason delete's allowlist is the wider one depends on that, so read the
    sentence above as naming edit and delete exhaustively. ``@runtime_checkable``
    lets tests assert an implementation satisfies the port with ``isinstance`` — a
    structural check on method presence only, not on signatures.
    """

    def preview_create(self, project: Project, draft: TicketDraft) -> DiffPreview:
        """Return the side-effect-free :class:`DiffPreview` of creating ``draft``."""
        ...

    def create_ticket(self, project: Project, draft: TicketDraft) -> WriteResult:
        """Create ``draft`` and return the applied :class:`WriteResult`.

        UNGATED BY DESIGN — no run-state check at all, unlike :meth:`edit_ticket` and
        :meth:`delete_ticket`. A brand-new id is by definition not listed by any
        resolved run-state source, so ANY gate here would resolve
        :attr:`~factory_console.domain.run_state.RunState.absent` and 409 every create
        in a project whose source is populated. :meth:`delete_ticket`'s wider allowlist
        exists precisely to undo what this ungatedness permits, so an implementation
        that gates create is not conforming (T80 amendment, gap 2).
        """
        ...

    def preview_edit(self, project: Project, ticket_id: str, edit: TicketEdit) -> DiffPreview:
        """Return the side-effect-free :class:`DiffPreview` of editing ``ticket_id``."""
        ...

    def edit_ticket(self, project: Project, ticket_id: str, edit: TicketEdit) -> WriteResult:
        """Apply ``edit`` to ``ticket_id`` and return its :class:`WriteResult`.

        Gated on :data:`~factory_console.file_adapter.write_gate.MUTABLE_STATES`
        (``todo``/``unknown``): a ticket a resolved run-state source does not list
        (``absent``) is REFUSED here, unlike in :meth:`delete_ticket`.
        """
        ...

    def preview_delete(self, project: Project, ticket_id: str) -> DiffPreview:
        """Return the side-effect-free :class:`DiffPreview` of deleting ``ticket_id``."""
        ...

    def delete_ticket(self, project: Project, ticket_id: str) -> WriteResult:
        """Delete ``ticket_id`` and return the applied :class:`WriteResult`.

        Gated on :data:`~factory_console.file_adapter.write_gate.DELETABLE_STATES` —
        :data:`~factory_console.file_adapter.write_gate.MUTABLE_STATES` PLUS
        :attr:`~factory_console.domain.run_state.RunState.absent`. Delete is
        deliberately wider than edit: ``create_ticket`` is ungated, so a ticket the
        console just minted resolves ``absent`` in any project with a populated
        run-state source, and refusing the delete would leave it unrecoverable
        through the very UI that created it (T80 amendment, gap 2).
        """
        ...
