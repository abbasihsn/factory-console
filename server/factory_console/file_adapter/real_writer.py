"""Filesystem-backed :class:`RealFileWriter` — the production FileWriter.

The write-side twin of :class:`~factory_console.file_adapter.real.RealFileAdapter`
and the disk-backed sibling of the in-memory
:class:`~factory_console.file_adapter.fake_writer.FakeFileWriter`. This is the
writer ``create_app``/``cli`` wire behind the ``FileWriter`` ``Depends()`` so the
v2 ``POST``/``PUT``/``DELETE`` endpoints get real, safe, atomic ticket writes.

Like the read adapter it is *stateless* (``RealFileWriter()`` takes no arguments
and caches nothing) and *composed*, not re-implemented: every method delegates to
the four single-purpose write modules rather than parsing manifests, rendering
``.md``, probing run-state, or opening files here.

* :mod:`~factory_console.file_adapter.write_render` computes the desired text of
  the three coupled files (manifest, ticket ``.md``, roadmap) as a pure
  ``PlannedChange`` set — raising ``TicketAlreadyExists``/``UnknownTicket``/
  ``PathTraversal`` for bad input.
* :func:`~factory_console.file_adapter.write_diff.preview` renders that set as the
  side-effect-free :class:`~factory_console.domain.write.DiffPreview` the UI and
  dry-run show.
* :func:`~factory_console.file_adapter.write_gate.ensure_mutable` is the todo-only
  authorization gate (409 ``TicketNotMutable`` for ``in-flight``/``ready``/
  ``merged``).
* :func:`~factory_console.file_adapter.atomic_write.apply_changes` is the ONE
  sanctioned write site — this class never opens, writes, or unlinks a file
  itself, and that layer independently refuses any run-state path.

Applied writes re-read the just-written ticket through a fresh
:class:`~factory_console.file_adapter.real.RealFileAdapter` so the returned
:class:`~factory_console.domain.write.WriteResult` carries the same
:class:`~factory_console.domain.ticket.Ticket` a subsequent read would — no
parallel enrichment path.
"""

from __future__ import annotations

from factory_console.domain import Project
from factory_console.domain.ticket import Ticket
from factory_console.domain.write import (
    DiffPreview,
    TicketDraft,
    TicketEdit,
    WriteResult,
)
from factory_console.file_adapter import atomic_write, write_diff, write_gate, write_render
from factory_console.file_adapter.manifest import load_manifest, manifest_entry_to_ticket_stub
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.ticket_md import TicketFileMissing


class RealFileWriter:
    """Filesystem-backed :class:`FileWriter` — the production writer.

    Stateless (``RealFileWriter()`` takes no arguments and caches nothing) and
    satisfies the ``@runtime_checkable``
    :class:`~factory_console.file_adapter.writer_protocol.FileWriter` Protocol
    structurally, so ``isinstance(RealFileWriter(), FileWriter)`` holds without
    inheritance. Preview methods are pure (no gate, no write); apply methods pass
    the todo-only mutability gate before routing every write through
    :func:`~factory_console.file_adapter.atomic_write.apply_changes`.
    """

    # ------------------------------------------------------------------ #
    # Preview (pure, side-effect-free) — return a DiffPreview
    # ------------------------------------------------------------------ #
    #
    # Previews carry NO mutability gate on purpose: they only read and diff, so a
    # non-todo ticket can still be previewed. The UI's disabled confirm button is
    # the UX guard; the hard 409 lives on the apply methods, where a write would
    # actually happen (ticket step 2).

    def preview_create(self, project: Project, draft: TicketDraft) -> DiffPreview:
        """Return the :class:`DiffPreview` of creating ``draft``, mutating nothing."""
        return write_diff.preview(draft.id, write_render.render_create(project, draft))

    def preview_edit(self, project: Project, ticket_id: str, edit: TicketEdit) -> DiffPreview:
        """Return the :class:`DiffPreview` of editing ``ticket_id``, mutating nothing."""
        return write_diff.preview(ticket_id, write_render.render_edit(project, ticket_id, edit))

    def preview_delete(self, project: Project, ticket_id: str) -> DiffPreview:
        """Return the :class:`DiffPreview` of deleting ``ticket_id``, mutating nothing."""
        return write_diff.preview(ticket_id, write_render.render_delete(project, ticket_id))

    # ------------------------------------------------------------------ #
    # Apply — gate, plan, write atomically, re-read, WriteResult
    # ------------------------------------------------------------------ #

    def create_ticket(self, project: Project, draft: TicketDraft) -> WriteResult:
        """Create ``draft`` on disk and return the applied :class:`WriteResult`.

        No mutability gate: a brand-new id has no factory run-state (it resolves to
        the mutable ``unknown``), matching :class:`FakeFileWriter`. Raises
        :class:`~factory_console.file_adapter.path_safety.PathTraversal` for an
        unsafe id and
        :class:`~factory_console.file_adapter.write_render.TicketAlreadyExists`
        (409) when the id is already in the manifest — both from
        :func:`~factory_console.file_adapter.write_render.render_create`.
        """
        planned = write_render.render_create(project, draft)  # validates id + duplicate
        preview = write_diff.preview(draft.id, planned)
        atomic_write.apply_changes(project, planned)
        ticket = self._reread(project, draft.id)
        return self._applied_result(draft.id, preview, ticket)

    def edit_ticket(self, project: Project, ticket_id: str, edit: TicketEdit) -> WriteResult:
        """Apply ``edit`` to ``ticket_id`` on disk and return the applied :class:`WriteResult`.

        Enforces the todo-only gate FIRST (ticket step 4): a non-todo run-state
        fails fast with :class:`~factory_console.file_adapter.write_gate.TicketNotMutable`
        (409) BEFORE any render or write. (Gate-first vs the fake's existence-first
        order is observationally equivalent — an unknown id resolves to the mutable
        ``unknown`` state, so the gate never masks the
        :class:`~factory_console.file_adapter.write_render.UnknownTicket` that
        :func:`~factory_console.file_adapter.write_render.render_edit` then raises
        for it.)
        """
        write_gate.ensure_mutable(project, ticket_id)
        planned = write_render.render_edit(project, ticket_id, edit)  # validates id + existence
        preview = write_diff.preview(ticket_id, planned)
        atomic_write.apply_changes(project, planned)
        ticket = self._reread(project, ticket_id)
        return self._applied_result(ticket_id, preview, ticket)

    def delete_ticket(self, project: Project, ticket_id: str) -> WriteResult:
        """Delete ``ticket_id`` from disk and return the applied :class:`WriteResult`.

        Gate FIRST (as :meth:`edit_ticket`), then plan and preview. The ticket's
        final on-disk state is re-read BEFORE :func:`apply_changes` removes it —
        after the delete it is gone, and :class:`WriteResult` requires ``ticket``
        set iff ``applied`` — mirroring how :class:`FakeFileWriter` snapshots the
        entry before removal.
        """
        write_gate.ensure_mutable(project, ticket_id)
        planned = write_render.render_delete(project, ticket_id)  # validates id + existence
        preview = write_diff.preview(ticket_id, planned)
        # Re-read the deleted ticket's FINAL state before the write erases its .md,
        # so the applied WriteResult can carry it (ticket-set-iff-applied invariant).
        # An orphaned entry (manifest present, .md already absent) makes the full
        # re-read RAISE (``_reread``'s own docstring says so) rather than return a
        # ticket — right for a READ, but a pre-delete snapshot must not block the
        # delete it only exists to describe, or the orphan could never be removed.
        # Falls back to a manifest-only snapshot, mirroring how FakeFileWriter
        # snapshots an unseeded body as ``""``.
        try:
            ticket = self._reread(project, ticket_id)
        except TicketFileMissing:
            ticket = self._manifest_only_snapshot(project, ticket_id)
        atomic_write.apply_changes(project, planned)
        return self._applied_result(ticket_id, preview, ticket)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _reread(project: Project, ticket_id: str) -> Ticket:
        """Re-read ``ticket_id`` as a full :class:`Ticket` via a fresh read adapter.

        Reuses the production read path so the returned ticket is byte-for-byte the
        one a subsequent GET would serve. ``get_ticket`` returns ``None`` only for
        an id absent from the manifest — impossible here, since the id was just
        created (or its existence validated before a pre-delete re-read) — so a
        ``None`` would be a broken invariant, asserted rather than silently coerced.
        A manifest ticket whose ``.md`` is missing surfaces as ``TicketFileMissing``
        (a real 404) from ``get_ticket`` and propagates, rather than being hidden.
        """
        ticket = RealFileAdapter().get_ticket(project, ticket_id)
        assert ticket is not None, f"ticket {ticket_id} vanished from the manifest after write"
        return ticket

    @staticmethod
    def _manifest_only_snapshot(project: Project, ticket_id: str) -> Ticket:
        """Build a bare :class:`Ticket` from the manifest entry alone, body empty.

        The pre-delete fallback for :meth:`delete_ticket` on an orphaned entry: its
        ``.md`` is already absent, so there is nothing to read a body from — mirrors
        :class:`FakeFileWriter`'s snapshot-before-removal, which defaults an
        unseeded body to ``""`` for the same reason.
        """
        _schema_version, entries = load_manifest(project.ticketsManifestPath)
        index = write_render._find_entry_index(entries, ticket_id)
        assert index is not None  # existence already validated by render_delete
        return manifest_entry_to_ticket_stub(entries[index], project.ticketsDir)

    @staticmethod
    def _applied_result(ticket_id: str, preview: DiffPreview, ticket: Ticket) -> WriteResult:
        """Build the ``applied=True`` :class:`WriteResult` from a preview + re-read ticket.

        ``changedFiles`` is derived from ``preview.files`` (not the relPaths
        :func:`apply_changes` returns) so it always agrees with the ``diff`` the
        caller sees — the same contract :class:`FakeFileWriter` keeps. The two lists
        can differ only on a no-op change: ``preview`` OMITS a change whose current
        text equals its new text, while ``apply_changes`` writes (and reports) every
        planned path; using the preview keeps ``changedFiles == diff.files``.
        """
        return WriteResult(
            applied=True,
            ticketId=ticket_id,
            changedFiles=[file.path for file in preview.files],
            diff=preview,
            ticket=ticket,
        )
