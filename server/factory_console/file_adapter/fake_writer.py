"""In-memory :class:`FakeFileWriter` — a side-effect-free FileWriter for tests.

The write-side counterpart of
:class:`~factory_console.file_adapter.fake.FakeFileAdapter`: seeded with an
in-memory manifest (the list of entry dicts), a ``{ticket_id: bodyMarkdown}`` map,
an optional roadmap body, and an optional ``{ticket_id: RunState}`` map, it answers
every :class:`~factory_console.file_adapter.writer_protocol.FileWriter` method as a
pure computation over that seeded state — no filesystem read, write, stat, or path
resolution ever happens.

Render semantics are NOT re-implemented here. The fake reuses the T57
``write_render`` PURE, module-level helpers (``_draft_to_entry`` / ``_merge_edit`` /
``_serialize_manifest`` / ``_render_md`` / the ``_roadmap_*`` transforms) and the T58
``write_diff.preview`` diff engine so a fake preview is computed by the identical
code the real writer plans from — the single source of truth for what a create/edit/
delete does, so the two can never drift. Only the *filesystem-touching* public
render functions (``render_create`` and friends, ``ensure_mutable``) are avoided;
their pure building blocks are deliberately reused across the module boundary
(hence the underscore imports below).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from factory_console.domain import Project, RunState, Ticket
from factory_console.domain.write import (
    DiffPreview,
    TicketDraft,
    TicketEdit,
    WriteResult,
)
from factory_console.file_adapter import write_diff, write_gate, write_render
from factory_console.file_adapter.manifest import manifest_entry_to_ticket_stub
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.write_render import PlannedChange


class FakeFileWriter:
    """In-memory :class:`FileWriter` implementation for deterministic tests.

    Satisfies the ``FileWriter`` Protocol structurally (no inheritance);
    ``isinstance(fake, FileWriter)`` holds because the Protocol is
    ``@runtime_checkable``. All state is held in the seeded dicts and computed
    from them — the fake never touches disk.
    """

    def __init__(
        self,
        manifest: list[dict[str, Any]],
        bodies: dict[str, str] | None = None,
        roadmap: str | None = None,
        run_states: dict[str, RunState] | None = None,
        front_matter: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Seed the writer with pre-resolved, in-memory project state.

        ``manifest`` is the list of manifest ENTRIES (the ``tickets`` list);
        ``bodies`` maps ``{ticket_id: bodyMarkdown}``; ``front_matter`` maps
        ``{ticket_id: frontMatter}`` for tickets whose ``.md`` carries a YAML
        fence (an unseeded id defaults to ``{}`` — a fence-less ``.md``);
        ``roadmap`` is the roadmap body text (or ``None`` for a project without
        one). ``run_states`` is normalized to ``{}`` when ``None`` (an unseeded id
        then resolves to :attr:`RunState.unknown`), exactly like
        :class:`FakeFileAdapter`. The seeded collections are copied so a caller's
        dicts are never mutated in place by an apply.

        ``front_matter`` is tracked separately from ``bodies`` because the ``.md``
        diff's ``currentText`` must be the FULL rendered file (fence + body, via
        :func:`write_render._render_md`), exactly what the real writer reads off
        disk — while :meth:`_entry_to_ticket` needs the fence-less body. Storing
        both keeps the fake's ``.md`` diff identical to the real writer's even for
        a front-matter-bearing ticket.
        """
        self._manifest = [dict(entry) for entry in manifest]
        self._bodies = {} if bodies is None else dict(bodies)
        self._front_matter = (
            {} if front_matter is None else {tid: dict(fm) for tid, fm in front_matter.items()}
        )
        self._roadmap = roadmap
        self._run_states = {} if run_states is None else dict(run_states)

    # ------------------------------------------------------------------ #
    # Preview (pure, side-effect-free) — return a DiffPreview
    # ------------------------------------------------------------------ #

    def preview_create(self, project: Project, draft: TicketDraft) -> DiffPreview:
        """Return the :class:`DiffPreview` of creating ``draft``, mutating nothing."""
        return write_diff.preview(draft.id, self._plan_create(project, draft))

    def preview_edit(self, project: Project, ticket_id: str, edit: TicketEdit) -> DiffPreview:
        """Return the :class:`DiffPreview` of editing ``ticket_id``, mutating nothing."""
        return write_diff.preview(ticket_id, self._plan_edit(project, ticket_id, edit))

    def preview_delete(self, project: Project, ticket_id: str) -> DiffPreview:
        """Return the :class:`DiffPreview` of deleting ``ticket_id``, mutating nothing."""
        return write_diff.preview(ticket_id, self._plan_delete(project, ticket_id))

    # ------------------------------------------------------------------ #
    # Apply — enforce the mutability gate, mutate seeded state, WriteResult
    # ------------------------------------------------------------------ #

    def create_ticket(self, project: Project, draft: TicketDraft) -> WriteResult:
        """Create ``draft`` in memory and return the applied :class:`WriteResult`.

        Raises :class:`PathTraversal` for an unsafe id and
        :class:`~factory_console.file_adapter.write_render.TicketAlreadyExists`
        (409) when the id is already an entry in the seeded manifest.
        """
        planned = self._plan_create(project, draft)  # validates id + duplicate
        preview = write_diff.preview(draft.id, planned)

        new_entry = write_render._draft_to_entry(draft)
        self._manifest.append(new_entry)
        self._bodies[draft.id] = draft.bodyMarkdown
        self._front_matter[draft.id] = dict(draft.frontMatter)
        self._apply_roadmap(project, planned)

        state = self._run_states.get(draft.id, RunState.unknown)
        ticket = self._entry_to_ticket(project, new_entry, draft.bodyMarkdown, state)
        return self._applied_result(draft.id, preview, ticket)

    def edit_ticket(self, project: Project, ticket_id: str, edit: TicketEdit) -> WriteResult:
        """Apply ``edit`` to ``ticket_id`` and return the applied :class:`WriteResult`.

        Validates existence FIRST (raising
        :class:`~factory_console.file_adapter.write_render.UnknownTicket`, 404, for
        an absent id), then enforces the todo-only mutability gate over the SEEDED
        run-state (raising :class:`~factory_console.file_adapter.write_gate.TicketNotMutable`,
        409, for a non-mutable state) — mirroring how the real write path composes
        the render check and the gate.
        """
        planned = self._plan_edit(project, ticket_id, edit)  # validates id + existence
        self._ensure_mutable(ticket_id)
        preview = write_diff.preview(ticket_id, planned)

        index = write_render._find_entry_index(self._manifest, ticket_id)
        assert index is not None  # existence already validated by _plan_edit
        merged = write_render._merge_edit(self._manifest[index], edit)
        self._manifest[index] = merged
        self._bodies[ticket_id] = edit.bodyMarkdown
        self._front_matter[ticket_id] = dict(edit.frontMatter)
        self._apply_roadmap(project, planned)

        state = self._run_states.get(ticket_id, RunState.unknown)
        ticket = self._entry_to_ticket(project, merged, edit.bodyMarkdown, state)
        return self._applied_result(ticket_id, preview, ticket)

    def delete_ticket(self, project: Project, ticket_id: str) -> WriteResult:
        """Delete ``ticket_id`` from memory and return the applied :class:`WriteResult`.

        Validates existence FIRST (:class:`UnknownTicket`, 404) then the todo-only
        gate (:class:`TicketNotMutable`, 409), exactly like :meth:`edit_ticket`.
        """
        planned = self._plan_delete(project, ticket_id)  # validates id + existence
        self._ensure_mutable(ticket_id)
        preview = write_diff.preview(ticket_id, planned)

        index = write_render._find_entry_index(self._manifest, ticket_id)
        assert index is not None  # existence already validated by _plan_delete
        # WriteResult requires ``ticket`` set iff ``applied``, and an apply is always
        # applied. Because delete removes the entry, its returned Ticket is built
        # from a SNAPSHOT of the entry (and body) captured BEFORE removal — the
        # just-deleted ticket's final state — so the invariant stays satisfiable.
        state = self._run_states.get(ticket_id, RunState.unknown)
        snapshot_entry = self._manifest[index]
        snapshot_body = self._bodies.get(ticket_id, "")

        del self._manifest[index]
        self._bodies.pop(ticket_id, None)
        self._front_matter.pop(ticket_id, None)
        self._apply_roadmap(project, planned)

        ticket = self._entry_to_ticket(project, snapshot_entry, snapshot_body, state)
        return self._applied_result(ticket_id, preview, ticket)

    # ------------------------------------------------------------------ #
    # Planning — build the PlannedChange set purely from seeded state
    # ------------------------------------------------------------------ #
    #
    # Each planner reuses T57's pure ``write_render`` helpers to compute the exact
    # text the three coupled files WOULD hold, so the fake never re-derives the
    # manifest-serialization / md-render / roadmap-rewrite semantics by hand.

    def _plan_create(self, project: Project, draft: TicketDraft) -> list[PlannedChange]:
        self._require_safe_id(draft.id)
        if write_render._find_entry_index(self._manifest, draft.id) is not None:
            raise write_render.TicketAlreadyExists(draft.id)
        new_entries = [*self._manifest, write_render._draft_to_entry(draft)]
        changes = [
            self._manifest_change(project, new_entries),
            self._md_change(
                project,
                draft.id,
                current=self._current_md(draft.id),
                new=write_render._render_md(draft.frontMatter, draft.bodyMarkdown),
            ),
        ]
        self._append_roadmap(
            changes,
            project,
            lambda current: write_render._roadmap_create_text(
                current, draft.milestone, draft.id, draft.title
            ),
        )
        return changes

    def _plan_edit(self, project: Project, ticket_id: str, edit: TicketEdit) -> list[PlannedChange]:
        self._require_safe_id(ticket_id)
        index = write_render._find_entry_index(self._manifest, ticket_id)
        if index is None:
            raise write_render.UnknownTicket(ticket_id)
        merged = write_render._merge_edit(self._manifest[index], edit)
        new_entries = list(self._manifest)
        new_entries[index] = merged
        changes = [
            self._manifest_change(project, new_entries),
            self._md_change(
                project,
                ticket_id,
                current=self._current_md(ticket_id),
                new=write_render._render_md(edit.frontMatter, edit.bodyMarkdown),
            ),
        ]
        self._append_roadmap(
            changes,
            project,
            lambda current: write_render._roadmap_edit_text(
                current, ticket_id, edit.milestone, edit.title
            ),
        )
        return changes

    def _plan_delete(self, project: Project, ticket_id: str) -> list[PlannedChange]:
        self._require_safe_id(ticket_id)
        index = write_render._find_entry_index(self._manifest, ticket_id)
        if index is None:
            raise write_render.UnknownTicket(ticket_id)
        new_entries = self._manifest[:index] + self._manifest[index + 1 :]
        changes = [
            self._manifest_change(project, new_entries),
            self._md_change(project, ticket_id, current=self._current_md(ticket_id), new=None),
        ]
        self._append_roadmap(
            changes,
            project,
            lambda current: write_render._roadmap_delete_text(current, ticket_id),
        )
        return changes

    # ------------------------------------------------------------------ #
    # PlannedChange builders — one per coupled file, all in-memory
    # ------------------------------------------------------------------ #

    def _manifest_change(
        self, project: Project, new_entries: list[dict[str, Any]]
    ) -> PlannedChange:
        """The manifest :class:`PlannedChange`: seeded-vs-mutated ``{"tickets": [...]}``.

        Both sides are serialized with the SAME ``write_render._serialize_manifest``
        the real renderer uses, so the fake's manifest diff matches on-disk format.
        """
        path = project.ticketsManifestPath
        return PlannedChange(
            path=path,
            relPath=self._rel_posix(path, project),
            currentText=write_render._serialize_manifest({"tickets": self._manifest}),
            newText=write_render._serialize_manifest({"tickets": new_entries}),
        )

    def _current_md(self, ticket_id: str) -> str | None:
        """Render the ticket's CURRENT ``.md`` file text, or ``None`` when absent.

        Mirrors the real writer's ``currentText=_read_text_or_none(md_path)``: an
        unseeded ticket (no body) has no file yet (``None`` → a create-like diff),
        while a seeded one renders the FULL file — YAML fence plus body — through the
        SAME :func:`write_render._render_md` that produces ``newText``, so both sides
        of the ``.md`` diff are rendered identically even when the ticket carries
        front-matter. A fence-less ticket (empty/absent front-matter) renders to the
        body verbatim, so this is a no-op for the common case.
        """
        if ticket_id not in self._bodies:
            return None
        return write_render._render_md(
            self._front_matter.get(ticket_id, {}), self._bodies[ticket_id]
        )

    def _md_change(
        self, project: Project, ticket_id: str, *, current: str | None, new: str | None
    ) -> PlannedChange:
        """The ticket ``.md`` :class:`PlannedChange` from the seeded body (no FS read)."""
        path = project.ticketsDir / f"{ticket_id}.md"
        return PlannedChange(
            path=path,
            relPath=self._rel_posix(path, project),
            currentText=current,
            newText=new,
        )

    def _append_roadmap(
        self,
        changes: list[PlannedChange],
        project: Project,
        transform: Callable[[str], str | None],
    ) -> None:
        """Append the roadmap :class:`PlannedChange`, mirroring ``_roadmap_change``.

        Emits a change only when the project HAS a roadmap path, a roadmap body is
        seeded, and ``transform`` yields a real, non-no-op rewrite of the SEEDED
        roadmap string (never the filesystem) — the exact conditions
        ``write_render._roadmap_change`` uses on disk.
        """
        if project.roadmapPath is None or self._roadmap is None:
            return
        new_text = transform(self._roadmap)
        if new_text is None or new_text == self._roadmap:
            return
        changes.append(
            PlannedChange(
                path=project.roadmapPath,
                relPath=self._rel_posix(project.roadmapPath, project),
                currentText=self._roadmap,
                newText=new_text,
            )
        )

    # ------------------------------------------------------------------ #
    # Apply helpers
    # ------------------------------------------------------------------ #

    def _apply_roadmap(self, project: Project, planned: list[PlannedChange]) -> None:
        """Replace the seeded roadmap string with the planned roadmap change, if any."""
        if project.roadmapPath is None:
            return
        for change in planned:
            if change.path == project.roadmapPath:
                self._roadmap = change.newText
                return

    def _ensure_mutable(self, ticket_id: str) -> None:
        """Enforce the todo-only gate over the SEEDED run-state (no FS probe).

        Mirrors :func:`~factory_console.file_adapter.write_gate.ensure_mutable`
        without ``probe_ticket_state``: an unseeded id resolves to
        :attr:`RunState.unknown` (mutable), any state outside
        :data:`~factory_console.file_adapter.write_gate.MUTABLE_STATES` raises.
        """
        state = self._run_states.get(ticket_id, RunState.unknown)
        if state not in write_gate.MUTABLE_STATES:
            raise write_gate.TicketNotMutable(ticket_id, state)

    def _entry_to_ticket(
        self, project: Project, entry: dict[str, Any], body_markdown: str, state: RunState
    ) -> Ticket:
        """Join a manifest entry with its body into a :class:`Ticket`.

        Reuses ``manifest_entry_to_ticket_stub`` — the canonical entry->Ticket
        mapper (single source for the ``provides`` scalar->list coercion and the
        ``filePath`` computation) — then overlays the seeded body and run-state.
        """
        stub = manifest_entry_to_ticket_stub(entry, project.ticketsDir)
        return stub.model_copy(update={"bodyMarkdown": body_markdown, "runState": state})

    @staticmethod
    def _applied_result(ticket_id: str, preview: DiffPreview, ticket: Ticket) -> WriteResult:
        """Build the ``applied=True`` :class:`WriteResult` from a preview + ticket.

        ``changedFiles`` is derived from the preview's files so it always agrees
        with the diff the caller sees.
        """
        return WriteResult(
            applied=True,
            ticketId=ticket_id,
            changedFiles=[file.path for file in preview.files],
            diff=preview,
            ticket=ticket,
        )

    @staticmethod
    def _require_safe_id(ticket_id: str) -> None:
        """Reject an unsafe ticket id lexically (no on-disk resolution needed).

        A regex check against ``write_render._TICKET_ID_RE`` is sufficient here:
        there is no real filesystem for a ``/``/``..`` id to escape, so the fake
        raises :class:`PathTraversal` on the pattern violation alone, mirroring
        T57's defense-in-depth id re-validation.
        """
        if write_render._TICKET_ID_RE.fullmatch(ticket_id) is None:
            raise PathTraversal.from_pattern_violation(ticket_id)

    @staticmethod
    def _rel_posix(path: Path, project: Project) -> str:
        """Project-relative POSIX path computed LEXICALLY — never ``.resolve()`` (it stats)."""
        return path.relative_to(project.rootPath).as_posix()
