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
  authorization gate for an EDIT (409 ``TicketNotMutable`` for ``in-flight``/
  ``ready``/``merged``, and for ``absent``);
  :func:`~factory_console.file_adapter.write_gate.ensure_deletable` is its
  delete-path sibling, identical but for also permitting ``absent`` — see
  :meth:`RealFileWriter.delete_ticket`.
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
from factory_console.file_adapter.real import RealFileAdapter


class RealFileWriter:
    """Filesystem-backed :class:`FileWriter` — the production writer.

    Stateless (``RealFileWriter()`` takes no arguments and caches nothing) and
    satisfies the ``@runtime_checkable``
    :class:`~factory_console.file_adapter.writer_protocol.FileWriter` Protocol
    structurally, so ``isinstance(RealFileWriter(), FileWriter)`` holds without
    inheritance. Preview methods are pure (no gate, no write); each apply method
    passes ITS OWN gate — :func:`~factory_console.file_adapter.write_gate.ensure_mutable`
    for :meth:`edit_ticket`, the wider
    :func:`~factory_console.file_adapter.write_gate.ensure_deletable` for
    :meth:`delete_ticket`, and none at all for :meth:`create_ticket` — before routing
    every write through
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

        No mutability gate: create is ungated by design, and a brand-new id's
        run-state is not consulted at all. Note what that id then resolves to on the
        NEXT request, because it is NOT uniform (T80): with no run-state source at
        all — or with one that is resolved but VACUOUS (an empty run-state directory,
        or ``run-state.json`` with an empty ``tickets`` object) — it is the mutable
        ``unknown``, because a source that names nobody claims nothing about anybody.
        Only in a project with a resolved and POPULATED source is it
        :attr:`~factory_console.domain.run_state.RunState.absent` — that source was
        consulted, lists other tickets, and does not list an id the factory has never
        seeded. Creating is
        therefore always allowed, while a follow-up :meth:`edit_ticket` on that same
        fresh id is refused 409 until the factory seeds it. That is the ticket's
        accepted consequence of refusing ``absent`` (T80 step 6, "a ticket in
        ``tickets.json`` but not in the run-state ... is now refused"), pinned by
        ``test_create_then_edit_is_refused_while_the_source_does_not_list_it``.
        :meth:`delete_ticket` is the deliberate exception — it gates on
        :func:`~factory_console.file_adapter.write_gate.ensure_deletable`, which
        permits ``absent``, so what create mints can always be un-created (T80's
        amendment, gap 2). The split is called out here because this docstring once
        claimed the mutable ``unknown`` for every project. Raises
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
        (409) BEFORE any render or write. Gate-first vs the fake's existence-first
        order is observationally equivalent whenever the project's run-state source is
        missing or VACUOUS, since both answer the mutable ``unknown``. It diverges for
        a resolved, POPULATED source: an id such a source does not list now (T80)
        answers :attr:`~factory_console.domain.run_state.RunState.absent`, so an id
        that is also absent from the manifest is refused by THIS gate (409) before it
        ever reaches the
        :class:`~factory_console.file_adapter.write_render.UnknownTicket` (404) that
        :func:`~factory_console.file_adapter.write_render.render_edit` would otherwise
        raise for it. That is intentional: "not known to the run-state" is the honest
        answer the gate has for such an id.

        That 409-before-404 ordering is not reachable through the wired API:
        :class:`~factory_console.services.write_service.WriteService` checks manifest
        existence and raises the canonical 404 before calling in here, so an id in
        neither the manifest nor the run-state is a 404 end-to-end. The ordering is
        observable only to a caller that drives this writer directly (the unit tests
        do), and is documented because the port permits both call orders.
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

        The gate is :func:`~factory_console.file_adapter.write_gate.ensure_deletable`,
        NOT ``ensure_mutable``: delete additionally permits
        :attr:`~factory_console.domain.run_state.RunState.absent`, so a ticket
        :meth:`create_ticket` just minted into a project with a populated run-state
        source can be removed again (T80's amendment, gap 2). Every other read-only
        state is refused here exactly as it is for an edit, and an edit of that same
        ``absent`` ticket stays refused.
        """
        write_gate.ensure_deletable(project, ticket_id)
        planned = write_render.render_delete(project, ticket_id)  # validates id + existence
        preview = write_diff.preview(ticket_id, planned)
        # Re-read the deleted ticket's FINAL state before the write erases its .md,
        # so the applied WriteResult can carry it (ticket-set-iff-applied invariant).
        ticket = self._reread(project, ticket_id)
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
