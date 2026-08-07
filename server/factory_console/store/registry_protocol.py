"""The :class:`ProjectRegistry` port — the seam over the console's own project rows.

Where :class:`~factory_console.file_adapter.protocol.FileAdapter` is the read seam
over a TARGET project's files, :class:`ProjectRegistry` is the seam over the
console's OWN durable state: which projects the user asked this console to track,
and which one they are currently looking at. The v3 registry endpoints depend on
it via ``FastAPI.Depends()``, so a handler adds or lists a project without ever
importing ``sqlite3``. It follows the three-file port shape this repo has used
twice (``protocol.py``/``fake.py``/``real.py``,
``writer_protocol.py``/``fake_writer.py``/``real_writer.py``); this module is the
Protocol only — the SQLite implementation and the in-memory fake land separately
and satisfy it structurally.

**The port is SYNCHRONOUS, deliberately.** Every read port in this codebase is,
and the backend offloads it at the handler boundary with
``await anyio.to_thread.run_sync(partial(...))`` per ARCHITECTURE.md's
Cross-cutting concurrency house rule — a blocking ``sqlite3`` call is that rule's
subject exactly as a blocking file read is. Making this the one ``async`` port
would fork the DI shape, the fake, and every test in the suite, for queries that
return single-digit row counts.

**The DATABASE is the authority on duplicates, not this Protocol's prose.** The
``UNIQUE`` index on ``projects.path`` is what makes two spellings of one directory
one row; :func:`~factory_console.store.paths.canonical_project_path` is what makes
the two spellings identical bytes for that index to compare. An implementation
must let the index decide and translate its violation into
:class:`DuplicateProjectPath` — never pre-check with a ``SELECT`` and treat that
as sufficient, which is a race with any concurrent writer, and never rely on a
check in the service layer above it.

**The port is TOTAL for reads.** An empty registry is ``[]``, an unknown id is
``None``, an unselected console is ``None``, and a removal of an unknown id is
``False``. None of those is an exception, because none of them is a failure — a
console that has never had a project added is in a perfectly ordinary state, and
a caller should not need a ``try`` to ask an ordinary question. The two errors
here are raised only by the two methods that are told to CHANGE something that
cannot be changed as asked.

Wiring, for the backend: provide this like
:func:`~factory_console.api.deps.get_file_adapter` — bound on ``app.state`` at
boot, with the provider RAISING when it is unbound — and NOT like the opt-in
:func:`~factory_console.api.deps.get_file_watcher`, which returns ``None`` so the
SSE endpoint can degrade. An unbound registry is a wiring bug, and because this
port is total there is nothing a ``None`` registry could honestly mean: every
question it answers already HAS an answer for an empty registry, so a ``None``
would make handlers invent a second, quieter kind of emptiness. Binding it always
costs a viewer session that never calls it nothing, since the real implementation
opens its database lazily (T108).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from factory_console.domain.registry import RegisteredProject
from factory_console.errors import FactoryConsoleError


class DuplicateProjectPath(FactoryConsoleError):
    """The canonical path is already registered under another row.

    Raised by :meth:`ProjectRegistry.add_project` when the ``UNIQUE`` index on
    ``projects.path`` refuses the insert. Status 409, because the request is
    well-formed and the conflict is with existing state — not 400, which would
    send the user to fix a path that is perfectly valid and simply already there.

    ``details`` carries ``{"path", "existingId"}``: the path so the message can
    name what collided, and the id of the row that already holds it so the client
    can offer "switch to it" instead of making the user hunt through the list for
    a project they cannot re-add. Both are already the caller's to see — the path
    is their own input (canonicalised) and the id is on every registry listing.

    Its home is this module rather than an implementation because its raise site
    is ANY conforming implementation, mirroring
    :class:`~factory_console.file_adapter.path_safety.PathTraversal`: the edge
    layer catches one exception type, not one per backing store.
    """

    def __init__(self, path: Path | str, existing_id: str) -> None:
        super().__init__(
            code="duplicate_project_path",
            message=f"A project is already registered at {path}",
            status=409,
            details={"path": str(path), "existingId": existing_id},
        )


class ProjectNotRegistered(FactoryConsoleError):
    """No registry row has the given id.

    Raised by :meth:`ProjectRegistry.set_selected_project` for an unknown id.
    Status 404: the id names a resource that is not there. ``details`` carries
    ``{"projectId"}`` — the caller's own input, echoed so a client handling
    several ids at once can tell which one failed.

    Deliberately NOT raised by the read methods or by
    :meth:`ProjectRegistry.remove_project`: those answer ``None``/``False``
    instead, per the module docstring's totality rule. Selection is the exception
    because it is the one call that would otherwise SUCCEED at pointing the whole
    console at a project that does not exist.
    """

    def __init__(self, project_id: str) -> None:
        super().__init__(
            code="project_not_registered",
            message=f"No registered project with id {project_id}",
            status=404,
            details={"projectId": project_id},
        )


@runtime_checkable
class ProjectRegistry(Protocol):
    """Seam between the registry endpoints and the console's own persistent store.

    Seven synchronous methods: four reads that are total (see the module
    docstring), two writes over the row set, and the selection pair. Every method
    that takes a path canonicalises it itself via
    :func:`~factory_console.store.paths.canonical_project_path`, so a caller may
    pass whatever spelling it was given and never has to pre-normalise — and two
    implementations cannot disagree about what "the same project" means.

    Selection lives on THIS port rather than behind a separate seam because
    "which project am I looking at" is registry state with a foreign key into
    registry rows. Splitting it would give the backend two ports to wire, two
    fakes to keep consistent, and two chances to disagree about one table's worth
    of state.

    ``@runtime_checkable`` lets tests assert an implementation satisfies the port
    with ``isinstance`` — a structural check on method PRESENCE only, never on
    signatures or on any of the behaviour these docstrings require.
    """

    def add_project(self, path: Path | str, name: str | None = None) -> RegisteredProject:
        """Register ``path`` and return the stored row.

        Canonicalises ``path``, mints a fresh ``uuid4().hex`` id matching
        :data:`~factory_console.domain.registry.REGISTERED_PROJECT_ID_PATTERN`,
        defaults ``name`` from the path via
        :func:`~factory_console.store.paths.default_project_name` when it is
        ``None``, and stamps ``addedAt`` as a timezone-aware UTC instant
        (``datetime.now(UTC)`` — the domain model rejects a naive one).

        Raises:
            InvalidProjectPath: ``path`` is blank, relative, or unresolvable.
            DuplicateProjectPath: the canonical path is already registered; the
                error carries the existing row's id.

        Explicitly does NOT validate that the path is an App Factory project, or
        that it exists at all. Registering a path on a volume that is currently
        unmounted MUST succeed and later read back as a NAMED
        :data:`~factory_console.domain.registry.RegistryEntryCondition` — a row
        records the user's intent, and refusing it here would mean a user cannot
        add the project they are about to plug in, while telling them their path
        is wrong.

        Explicitly does NOT auto-select the new project either. Conflating
        registration with selection hides a policy decision ("adding switches
        you") inside a write, and would yank the board out from under a user
        adding a second project while reading the first. The caller selects, by
        calling :meth:`set_selected_project`.
        """
        ...

    def list_projects(self) -> list[RegisteredProject]:
        """Return every registered row, in a stable UI order.

        Ordered by ``addedAt``, then by ``id`` to break ties — two rows added in
        the same clock tick must not swap places between two renders of the
        project switcher, which is what an unordered query would allow. Returns
        ``[]`` for an empty registry and never raises for one: a console nobody
        has added a project to yet is an ordinary state, not an error.
        """
        ...

    def get_project(self, project_id: str) -> RegisteredProject | None:
        """Return the row with ``project_id``, or ``None`` when there is none.

        ``None`` rather than a raise, per the module docstring's totality rule —
        the edge layer turns it into a 404 where a 404 is the right answer, and
        other callers simply branch.
        """
        ...

    def find_by_path(self, path: Path | str) -> RegisteredProject | None:
        """Return the row registered at ``path``, or ``None``.

        Canonicalises ``path`` first, so the caller's spelling does not matter:
        ``~/dev/foo``, ``/Users/me/dev/foo`` and a symlinked alias all find the
        same row. This is how the backend answers "is this already registered?"
        WITHOUT provoking a 409 out of :meth:`add_project` — an add-if-absent
        flow or a pre-flight UI check reads through here.

        Raises:
            InvalidProjectPath: ``path`` cannot be canonicalised. The lookup is
                total over registered rows, not over unusable input: there is no
                canonical form to compare, so ``None`` would assert "not
                registered" about a question that was never asked.
        """
        ...

    def remove_project(self, project_id: str) -> bool:
        """Delete the row with ``project_id``. ``True`` if one was removed.

        ``False`` when the id was unknown, so the edge layer can decide between
        404 and idempotent-204 on its own, without a raise/catch round trip for
        the ordinary case of a double-clicked delete button.

        Removing the SELECTED project CLEARS the selection — the schema's
        ``ON DELETE SET NULL`` — rather than leaving a dangling id behind. The
        console then has no project selected, which
        :meth:`get_selected_project` reports as ``None`` and the UI already knows
        how to render; a dangling id would instead make every subsequent read
        fail against a row that no longer exists.
        """
        ...

    def get_selected_project(self) -> RegisteredProject | None:
        """Return the persisted selection, or ``None`` when nothing is selected.

        The no-fallback rule, which is normative: an implementation MUST NOT
        substitute "the first project" (or the most recent, or any other guess)
        when the selection is unset or was cleared. Doing so renders one
        project's board under a heading the user last set to another — silently
        answering a question about project A with project B's tickets, run-state
        and spend. ``None`` means "nothing is selected", the UI prompts, and the
        user chooses.
        """
        ...

    def set_selected_project(self, project_id: str | None) -> RegisteredProject | None:
        """Select ``project_id``, or clear the selection with ``None``.

        Returns the newly selected row, or ``None`` when the selection was
        cleared — so a caller can render the result without a follow-up
        :meth:`get_selected_project`.

        Raises:
            ProjectNotRegistered: ``project_id`` names no row. The one write
                whose unknown id RAISES rather than answering falsely, because
                the alternative is a console that reports success while pointing
                at nothing (see :class:`ProjectNotRegistered`).
        """
        ...
