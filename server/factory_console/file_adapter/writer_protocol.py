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
WOULD change, while its apply sibling enforces the todo-only mutability gate,
performs the write, and returns a :class:`~factory_console.domain.write.WriteResult`.
Every method takes the resolved :class:`~factory_console.domain.project.Project`
first, mirroring ``FileAdapter``'s shape.
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
    the apply methods enforce the todo-only mutability gate before writing and
    return a :class:`~factory_console.domain.write.WriteResult`. ``@runtime_checkable``
    lets tests assert an implementation satisfies the port with ``isinstance`` — a
    structural check on method presence only, not on signatures.
    """

    def preview_create(self, project: Project, draft: TicketDraft) -> DiffPreview:
        """Return the side-effect-free :class:`DiffPreview` of creating ``draft``."""
        ...

    def create_ticket(self, project: Project, draft: TicketDraft) -> WriteResult:
        """Create ``draft`` and return the applied :class:`WriteResult`."""
        ...

    def preview_edit(self, project: Project, ticket_id: str, edit: TicketEdit) -> DiffPreview:
        """Return the side-effect-free :class:`DiffPreview` of editing ``ticket_id``."""
        ...

    def edit_ticket(self, project: Project, ticket_id: str, edit: TicketEdit) -> WriteResult:
        """Apply ``edit`` to ``ticket_id`` (todo-only gate) and return its :class:`WriteResult`."""
        ...

    def preview_delete(self, project: Project, ticket_id: str) -> DiffPreview:
        """Return the side-effect-free :class:`DiffPreview` of deleting ``ticket_id``."""
        ...

    def delete_ticket(self, project: Project, ticket_id: str) -> WriteResult:
        """Delete ``ticket_id`` (todo-only gate) and return the applied :class:`WriteResult`."""
        ...
