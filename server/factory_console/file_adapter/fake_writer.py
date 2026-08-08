"""In-memory :class:`FakeFileWriter` — a side-effect-free FileWriter for tests.

The write-side counterpart of
:class:`~factory_console.file_adapter.fake.FakeFileAdapter`: seeded with an
in-memory manifest (the list of entry dicts), a ``{ticket_id: TicketContentFields}``
map, an optional roadmap body, and an optional ``{ticket_id: RunState}`` map, it answers
every :class:`~factory_console.file_adapter.writer_protocol.FileWriter` method as a
pure computation over that seeded state — no filesystem read, write, stat, or path
resolution ever happens.

Render semantics are NOT re-implemented here. The fake reuses the T57
``write_render`` PURE, module-level helpers (``_draft_to_entry`` / ``_merge_edit`` /
``_serialize_manifest`` / ``_render_ticket_json`` / the ``_roadmap_*`` transforms) and the T58
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
    TicketContentFields,
    TicketDraft,
    TicketEdit,
    WriteResult,
)
from factory_console.file_adapter import ticket_json, write_diff, write_gate, write_render
from factory_console.file_adapter.manifest import manifest_entry_to_ticket_stub
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.ticket_json import render_ticket_markdown
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
        contents: dict[str, TicketContentFields] | None = None,
        roadmap: str | None = None,
        run_states: dict[str, RunState] | None = None,
    ) -> None:
        """Seed the writer with pre-resolved, in-memory project state.

        ``manifest`` is the list of manifest ENTRIES (the ``tickets`` list);
        ``contents`` maps ``{ticket_id: TicketContentFields}`` — the five structured
        fields an App Factory v3 ticket's own file carries; ``roadmap`` is the roadmap
        body text (or ``None`` for a project without one). ``run_states`` is normalized
        to ``{}`` when ``None`` (an unseeded id then resolves to
        :attr:`RunState.unknown`), exactly like :class:`FakeFileAdapter`. The seeded
        collections are copied so a caller's dicts are never mutated in place by an apply.

        ``contents`` REPLACED a ``bodies`` map of Markdown plus a parallel
        ``front_matter`` map, and the pair is gone rather than renamed. They existed
        because a ``.md`` diff's ``currentText`` had to be the full rendered file (fence
        plus body) while the ticket model needed the fence-less body, so the fake had to
        store the two halves separately to render both. A v3 content file has no such
        split: one structured value renders the whole file AND the Markdown view, so one
        map is the honest shape.

        :class:`~factory_console.domain.write.TicketContentFields` rather than a plain
        dict, deliberately — a seeded ticket is then constrained by the same model the
        write DTOs are, so a fixture cannot seed a ticket the write path would refuse to
        produce.
        """
        self._manifest = [dict(entry) for entry in manifest]
        self._contents = {} if contents is None else dict(contents)
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

        new_entry = write_render._draft_to_entry(draft, self._content_relpath(draft.id))
        self._manifest.append(new_entry)
        self._contents[draft.id] = draft
        self._apply_roadmap(project, planned)

        state = self._run_states.get(draft.id, RunState.unknown)
        ticket = self._entry_to_ticket(project, new_entry, draft, state)
        return self._applied_result(draft.id, preview, ticket)

    def edit_ticket(self, project: Project, ticket_id: str, edit: TicketEdit) -> WriteResult:
        """Apply ``edit`` to ``ticket_id`` and return the applied :class:`WriteResult`.

        Validates existence FIRST (raising
        :class:`~factory_console.file_adapter.write_render.UnknownTicket`, 404, for
        an absent id), then enforces the EDIT mutability gate (``todo``/``unknown``,
        NOT the wider delete allowlist) over the SEEDED
        run-state (raising :class:`~factory_console.file_adapter.write_gate.TicketNotMutable`,
        409, for a non-mutable state).

        That existence-first order matches :class:`RealFileWriter` ONLY for a project
        with no run-state source; since T80 it is NOT a general mirror of it. The real
        writer gates FIRST, and against a resolved source an id it does not list
        answers :attr:`RunState.absent` (409) rather than the mutable ``unknown`` — so
        for an unheard-of id the real writer raises ``TicketNotMutable`` where this
        fake raises ``UnknownTicket``, and a just-created id is refused here only if
        seeded ``absent``. See :meth:`_ensure_mutable` for the full divergence note;
        pin order-sensitive and create-then-edit expectations against
        :class:`RealFileWriter`, not this fake.
        """
        planned = self._plan_edit(project, ticket_id, edit)  # validates id + existence
        self._ensure_mutable(ticket_id)
        preview = write_diff.preview(ticket_id, planned)

        index = write_render._find_entry_index(self._manifest, ticket_id)
        assert index is not None  # existence already validated by _plan_edit
        merged = write_render._merge_edit(self._manifest[index], edit)
        self._manifest[index] = merged
        # REPLACE, not overlay. The real writer replaces the content file for the reason
        # its docstring gives — the schema forbids extra keys and requires every field,
        # so there is nothing a merge could preserve — and the seeded state must follow
        # or the fake's post-apply reads would diverge from production.
        self._contents[ticket_id] = edit
        self._apply_roadmap(project, planned)

        state = self._run_states.get(ticket_id, RunState.unknown)
        ticket = self._entry_to_ticket(project, merged, edit, state)
        return self._applied_result(ticket_id, preview, ticket)

    def delete_ticket(self, project: Project, ticket_id: str) -> WriteResult:
        """Delete ``ticket_id`` from memory and return the applied :class:`WriteResult`.

        Validates existence FIRST (:class:`UnknownTicket`, 404) then the gate
        (:class:`TicketNotMutable`, 409), in the same order as :meth:`edit_ticket` —
        including that method's T80 divergence from :class:`RealFileWriter`, which
        gates first. The gate itself is :meth:`_ensure_deletable`, not
        :meth:`_ensure_mutable`: delete additionally permits a seeded
        :attr:`RunState.absent`, matching
        :meth:`~factory_console.file_adapter.real_writer.RealFileWriter.delete_ticket`
        so the console can always remove a ticket it just created.
        """
        planned = self._plan_delete(project, ticket_id)  # validates id + existence
        self._ensure_deletable(ticket_id)
        preview = write_diff.preview(ticket_id, planned)

        index = write_render._find_entry_index(self._manifest, ticket_id)
        assert index is not None  # existence already validated by _plan_delete
        # WriteResult requires ``ticket`` set iff ``applied``, and an apply is always
        # applied. Because delete removes the entry, its returned Ticket is built
        # from a SNAPSHOT of the entry (and body) captured BEFORE removal — the
        # just-deleted ticket's final state — so the invariant stays satisfiable.
        state = self._run_states.get(ticket_id, RunState.unknown)
        snapshot_entry = self._manifest[index]
        snapshot_content = self._contents.get(ticket_id)

        del self._manifest[index]
        self._contents.pop(ticket_id, None)
        self._apply_roadmap(project, planned)

        ticket = self._entry_to_ticket(project, snapshot_entry, snapshot_content, state)
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
        new_entries = [
            *self._manifest,
            write_render._draft_to_entry(draft, self._content_relpath(draft.id)),
        ]
        changes = [
            self._manifest_change(project, new_entries),
            self._content_change(
                project,
                draft.id,
                current=self._current_content(draft.id),
                new=write_render._render_ticket_json(draft.id, draft),
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
        # The seeded entry decides the format, exactly as the on-disk entry does for the
        # real writer: an entry declaring a ``.md`` path is refused with the migration
        # command. An entry declaring NO path defaults to writable, because that is what
        # this fake's own creates produce and what every pre-v3 test fixture seeds — a
        # test that wants the refusal seeds the ``.md`` path explicitly.
        write_render._require_writable_format(
            ticket_id,
            Path(str(self._manifest[index].get("path") or self._content_relpath(ticket_id))),
        )
        merged = write_render._merge_edit(self._manifest[index], edit)
        new_entries = list(self._manifest)
        new_entries[index] = merged
        changes = [
            self._manifest_change(project, new_entries),
            self._content_change(
                project,
                ticket_id,
                current=self._current_content(ticket_id),
                # Through the SAME renderer as the real writer, over the same fields —
                # so the two implementations of this port cannot disagree about what an
                # edit writes to a content file.
                new=write_render._render_ticket_json(ticket_id, edit),
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
            self._content_change(
                project, ticket_id, current=self._current_content(ticket_id), new=None
            ),
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

    def _current_content(self, ticket_id: str) -> str | None:
        """Render the ticket's CURRENT content file text, or ``None`` when absent.

        Mirrors the real writer's ``currentText=_read_content_or_none(...)``: an unseeded
        ticket has no file yet (``None`` → a create-like diff), while a seeded one renders
        through the SAME :func:`write_render._render_ticket_json` that produces
        ``newText``, so both sides of the diff are rendered identically and a diff never
        shows churn that is only a difference in how the two sides were serialized.
        """
        content = self._contents.get(ticket_id)
        if content is None:
            return None
        return write_render._render_ticket_json(ticket_id, content)

    @staticmethod
    def _content_relpath(ticket_id: str) -> str:
        """The project-relative path a fake-created ticket's content file takes.

        Mirrors :func:`write_render._content_path_for_create` — flat, under the tickets
        directory, ``.json`` — but computed LEXICALLY from the known layout, because this
        fake resolves no paths and stats nothing.
        """
        return f"docs/planning/tickets/{ticket_id}{write_render._CONTENT_SUFFIX}"

    def _content_change(
        self, project: Project, ticket_id: str, *, current: str | None, new: str | None
    ) -> PlannedChange:
        """The content-file :class:`PlannedChange` from the seeded fields (no FS read)."""
        path = project.ticketsDir / f"{ticket_id}{write_render._CONTENT_SUFFIX}"
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
        """Enforce the EDIT gate over the SEEDED run-state (no FS probe).

        Mirrors :func:`~factory_console.file_adapter.write_gate.ensure_mutable`
        without ``probe_ticket_state``: an unseeded id resolves to
        :attr:`RunState.unknown` (mutable), any state outside
        :data:`~factory_console.file_adapter.write_gate.MUTABLE_STATES` raises.

        KNOWN DIVERGENCE from the real gate since T80, and the reason this default
        is not simply "correct": the real gate answers :attr:`RunState.absent`
        (refused, 409) for an id a RESOLVED run-state source does not list, whereas
        this fake has no source to resolve and so keeps every unseeded id mutable.
        Seed ``run_states={id: RunState.absent}`` to exercise the refusal. A test
        that asserts a write SUCCEEDS for an unseeded id therefore pins the fake's
        convenience, not production behaviour — assert those against
        :class:`RealFileWriter`.
        """
        self._ensure_state_allowed(ticket_id, write_gate.MUTABLE_STATES)

    def _ensure_deletable(self, ticket_id: str) -> None:
        """Enforce the DELETE gate over the SEEDED run-state (no FS probe).

        The fake's counterpart of
        :func:`~factory_console.file_adapter.write_gate.ensure_deletable`: the same
        allowlist, :data:`~factory_console.file_adapter.write_gate.DELETABLE_STATES`,
        which permits :attr:`RunState.absent` where :meth:`_ensure_mutable` refuses
        it. It exists because the fake CAN be seeded ``absent`` — so without it a
        test seeding ``absent`` would see the fake refuse a delete the real writer
        performs, which is the one thing a fake gate must never do. The divergence
        noted on :meth:`_ensure_mutable` is about the UNSEEDED default only; for a
        seeded state the two writers now agree on the allow/refuse DECISION for both
        edit and delete.

        They do NOT agree on the refusal MESSAGE, and cannot: the real gate names the
        resolved run-state source in its ``absent`` message
        (:class:`~factory_console.file_adapter.write_gate.TicketNotMutable`), and this
        fake has no source to name — a seeded state is not read from one. So a seeded
        ``absent`` refusal here carries the generic prose. Pin the ``absent`` 409 BODY
        against :class:`~factory_console.file_adapter.real_writer.RealFileWriter`;
        ``details`` (``ticketId``/``runState``) is identical either way, so a test that
        switches on the state rather than the prose holds against both.
        """
        self._ensure_state_allowed(ticket_id, write_gate.DELETABLE_STATES)

    def _ensure_state_allowed(self, ticket_id: str, allowed: tuple[RunState, ...]) -> None:
        """Raise :class:`TicketNotMutable` unless the seeded state is in ``allowed``."""
        state = self._run_states.get(ticket_id, RunState.unknown)
        if state not in allowed:
            raise write_gate.TicketNotMutable(ticket_id, state)

    def _entry_to_ticket(
        self,
        project: Project,
        entry: dict[str, Any],
        content: TicketContentFields | None,
        state: RunState,
    ) -> Ticket:
        """Join a manifest entry with its content fields into a :class:`Ticket`.

        Reuses ``manifest_entry_to_ticket_stub`` — the canonical entry->Ticket mapper
        (single source for the ``provides`` scalar->list coercion and the ``filePath``
        computation) — then overlays the rendered body, the content file's
        ``criticalFiles`` and the run-state.

        ``bodyMarkdown`` goes through the SAME
        :func:`~factory_console.file_adapter.ticket_json.render_ticket_markdown` the read
        path uses, so the ticket this fake hands back after an apply is the ticket a
        subsequent read would produce. ``content`` and ``files`` are set the way
        :func:`~factory_console.file_adapter.ticket_content.enrich_ticket` sets them — the
        structured fields published as-is and ``files`` taken from their ``criticalFiles``
        — because v3's index has no ``files`` key, so the content file is the only thing
        that answers, and because a fake whose applied ticket lacked ``content`` would let
        a test pass on a payload no real read ever returns.

        ``content is None`` is a real state, not a defensive default — a manifest entry
        seeded with no content fields, which the delete path also passes for a ticket
        that never had any. It leaves all three fields at the stub's values rather than
        inventing an empty body.
        """
        stub = manifest_entry_to_ticket_stub(entry, project.ticketsDir)
        if content is None:
            return stub.model_copy(update={"runState": state})
        parsed = ticket_json.parse_ticket_content(
            entry["id"], write_render._render_ticket_json(entry["id"], content)
        )
        return stub.model_copy(
            update={
                "bodyMarkdown": render_ticket_markdown(parsed, entry),
                "content": content,
                "files": list(content.criticalFiles),
                "runState": state,
            }
        )

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
