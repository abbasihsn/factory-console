"""Ticket create/edit/delete application service — the write analogue of ``TicketService``.

:class:`WriteService` holds the request logic for the three write endpoints so the
HTTP handlers stay wiring-only: it rejects id collisions on create, plans the
unified diff for a dry-run, and otherwise commits through the injected
:class:`~factory_console.file_adapter.writer_protocol.FileWriter`, then re-reads the
resulting :class:`~factory_console.domain.ticket.Ticket` through the read-only
:class:`~factory_console.file_adapter.protocol.FileAdapter` so the applied
:class:`~factory_console.domain.write.WriteResult` carries the post-write state.

It owns two co-located :class:`~factory_console.errors.FactoryConsoleError`
subclasses per the ``errors.py`` convention (the raiser and its error live together,
so the ONE existing exception handler renders them with no handler change):
:class:`WriteConflict` (create id collision) and :class:`WriteValidationError` (the
write-boundary 422). The non-todo mutability gate is enforced INSIDE the writer via
:class:`~factory_console.file_adapter.write_gate.TicketNotMutable` (409), which
propagates through this service unchanged — WriteService does NOT redefine it. Absent
ids on edit/delete reuse the canonical read-side
:class:`~factory_console.services.ticket_service.TicketNotFound` (404) rather than a
second not-found type. The service depends only on the two ports, never on a concrete
adapter/writer or the filesystem.
"""

from __future__ import annotations

from factory_console.domain import Project
from factory_console.domain.write import DiffPreview, TicketDraft, TicketEdit, WriteResult
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.ticket_md import TicketFileMissing
from factory_console.file_adapter.writer_protocol import FileWriter
from factory_console.services.ticket_service import TicketNotFound


class WriteConflict(FactoryConsoleError):
    """Raised when creating a ticket whose id already exists in the target project.

    Carries the ``write_conflict`` code at HTTP 409; the app-level domain-error
    handler renders it to the REST v1 envelope, so the create handler never catches
    it. This is the create-collision guard the service enforces BEFORE the writer runs
    (the writer's ``TicketAlreadyExists`` is an unreachable backstop behind it).
    """

    def __init__(self, ticket_id: str) -> None:
        super().__init__(
            code="write_conflict",
            message=f"Ticket {ticket_id!r} already exists",
            status=409,
            details={"ticketId": ticket_id},
        )


class WriteValidationError(FactoryConsoleError):
    """The canonical write-path validation error, rendered at HTTP 422.

    The write-boundary validation error for the create/edit/delete endpoints: the
    DTO layer (Pydantic ``extra='forbid'`` plus the ``TICKET_ID_PATTERN`` id check)
    validates request bodies at the FastAPI boundary, and ``write_render`` /
    ``write_gate`` own the 404/409 conditions, so there is no service-level raise-site
    inside the three methods today. It is defined here per the ``errors.py``
    co-location convention as the single write-path 422 the one existing
    :class:`~factory_console.errors.FactoryConsoleError` handler renders, keeping the
    write error contract complete.
    """

    def __init__(self, message: str, details: object | None = None) -> None:
        super().__init__(
            code="write_validation_error",
            message=message,
            status=422,
            details=details,
        )


class WriteService:
    """Orchestrates create/edit/delete writes over a ``FileWriter`` + ``FileAdapter``.

    Constructed per request with the injected write and read ports; holds no state
    beyond them. Every method returns the uniform :class:`WriteResult` envelope: a
    dry-run wraps the writer's ``preview_*`` :class:`DiffPreview` as ``applied=False``,
    while an apply commits through the writer and re-reads the resulting ticket through
    the adapter so the envelope carries the post-write state.
    """

    def __init__(self, writer: FileWriter, adapter: FileAdapter) -> None:
        self._writer = writer
        self._adapter = adapter

    def create(self, project: Project, payload: TicketDraft, *, dry_run: bool) -> WriteResult:
        """Create a ticket from ``payload`` (or preview the create when ``dry_run``).

        Rejects an id collision FIRST — on BOTH paths, since previewing a create for
        an id that already exists is misleading — by raising :class:`WriteConflict`
        when the adapter already resolves the id. Otherwise a dry-run returns the
        writer's planned :class:`DiffPreview` as ``applied=False``; an apply commits
        through the writer and returns its :class:`WriteResult` carrying the re-read
        ticket (see :meth:`_with_reread`).
        """
        if self._exists(project, payload.id):
            raise WriteConflict(payload.id)
        if dry_run:
            return self._dry_run_result(payload.id, self._writer.preview_create(project, payload))
        result = self._writer.create_ticket(project, payload)
        return self._with_reread(project, payload.id, result)

    def edit(
        self, project: Project, ticket_id: str, payload: TicketEdit, *, dry_run: bool
    ) -> WriteResult:
        """Apply ``payload`` to ``ticket_id`` (or preview the edit when ``dry_run``).

        Checks existence FIRST — on BOTH paths — raising the canonical read-side
        :class:`TicketNotFound` (404) when the adapter has no ticket for the id, then
        delegates editability to the writer: a dry-run returns the writer's planned
        :class:`DiffPreview` as ``applied=False``; an apply calls ``edit_ticket``,
        whose gate raises :class:`~factory_console.file_adapter.write_gate.TicketNotMutable`
        (409) for a non-todo ticket — that propagates unchanged — then re-reads the
        edited ticket through the adapter (see :meth:`_with_reread`).
        """
        if not self._exists(project, ticket_id):
            raise TicketNotFound(ticket_id)
        if dry_run:
            return self._dry_run_result(
                ticket_id, self._writer.preview_edit(project, ticket_id, payload)
            )
        result = self._writer.edit_ticket(project, ticket_id, payload)
        return self._with_reread(project, ticket_id, result)

    def delete(self, project: Project, ticket_id: str, *, dry_run: bool) -> WriteResult:
        """Delete ``ticket_id`` (or preview the delete when ``dry_run``).

        Checks existence FIRST — on BOTH paths — raising :class:`TicketNotFound` (404)
        when absent, then delegates editability to the writer: a dry-run returns the
        writer's planned :class:`DiffPreview` as ``applied=False``; an apply calls
        ``delete_ticket``, whose gate raises
        :class:`~factory_console.file_adapter.write_gate.TicketNotMutable` (409) for a
        non-todo ticket (propagated unchanged).

        Unlike create/edit, the delete apply does NOT re-read via the adapter: the
        ticket is GONE afterwards, so a re-read would be ``None`` and would violate
        :class:`WriteResult`'s ``ticket-iff-applied`` invariant. The writer's returned
        result already carries the pre-delete snapshot ticket, so it is returned
        verbatim.
        """
        if not self._exists(project, ticket_id):
            raise TicketNotFound(ticket_id)
        if dry_run:
            return self._dry_run_result(ticket_id, self._writer.preview_delete(project, ticket_id))
        return self._writer.delete_ticket(project, ticket_id)

    def _exists(self, project: Project, ticket_id: str) -> bool:
        """Whether ``ticket_id`` has a manifest entry, tolerant of a missing ``.md``.

        ``get_ticket`` is a full enrich-and-render read, so a manifest entry whose
        ``.md`` is absent (an orphan left by a partial factory write) makes it RAISE
        ``TicketFileMissing`` rather than return ``None``. Used only as a presence
        test here, so that case means "present, body absent" — the writer's own
        create/edit/delete paths already treat a missing body as a create-like edit,
        not a failure — never "absent"; otherwise the orphan could never be edited,
        deleted, or recreated.
        """
        try:
            return self._adapter.get_ticket(project, ticket_id) is not None
        except TicketFileMissing:
            return True

    def _dry_run_result(self, ticket_id: str, preview: DiffPreview) -> WriteResult:
        """Wrap a writer ``preview_*`` :class:`DiffPreview` as an ``applied=False`` result.

        The uniform dry-run envelope shared by all three write methods: it carries the
        planned ``changedFiles`` (derived from the preview so they always agree with the
        ``diff``) and no ``ticket``, mirroring :class:`RealFileWriter`'s ``_applied_result``
        on the apply side rather than repeating the construction at each call site.
        """
        return WriteResult(
            applied=False,
            ticketId=ticket_id,
            changedFiles=[file.path for file in preview.files],
            diff=preview,
            ticket=None,
        )

    def _with_reread(self, project: Project, ticket_id: str, result: WriteResult) -> WriteResult:
        """Return ``result`` carrying the ticket re-read through the ``FileAdapter``.

        After a create/edit apply, the authoritative post-write ticket is what the
        read adapter now resolves (in production both ports share one filesystem), so
        the returned envelope carries THAT ticket rather than the writer's in-line
        snapshot. Falls back to the writer's own ticket when the re-read is ``None``
        (which should not happen post-create/edit) so the ``ticket-iff-applied``
        invariant on an ``applied=True`` result can never break.
        """
        reread = self._adapter.get_ticket(project, ticket_id)
        if reread is None:
            return result
        return result.model_copy(update={"ticket": reread})
