"""Which project is this console looking at — and what it means when it cannot say.

v3.0 turns a single-project viewer into a multi-project one. The domain and the
services already take a :class:`~factory_console.domain.project.Project`, so the
only thing that has to move is the RESOLUTION: instead of every handler re-deriving
the one root ``create_app`` fixed at boot, one dependency
(:func:`~factory_console.api.deps.get_current_project_root`) answers "which root, for
THIS request?". This module owns the state that dependency reads and the vocabulary
it fails with.

**The precedence rule, which was the undefined thing.** Two candidate sources exist:
the root discovered from ``factory-console PATH``, and the selection persisted in the
console's own ``console_state`` row. Neither may simply win. If the pinned PATH always
won, the persisted selection would be permanently shadowed and switching projects
would change nothing any endpoint reads — the milestone's headline feature would be
inert in the only invocation it ships, since v3.0 ships no pathless boot. If the
persisted selection always won, ``factory-console /some/path`` would silently serve a
DIFFERENT project than the path the operator typed, contradicting the CLI contract and
its own ``serving <root>`` stdout line.

So: **the pinned PATH is the SESSION's INITIAL selection.** A process-local
``session_selection`` is seeded to :data:`SESSION_PROJECT_ID` at boot whenever a pin
exists, and is OVERWRITTEN in-process by a later :meth:`SelectionState.select` — which
also persists to the registry, so a future pathless boot resumes where the operator
left off. Both properties hold at once: the typed path is what you get, and switching
works.

**The selection is persisted, not owned here.** :class:`SelectionState` is a thin
read-through over the registry's ``get_selected_project``/``set_selected_project``. The
database row is the durable authority, and its ``ON DELETE SET NULL`` is what makes
"removing the selected project cannot leave a dangling selection" a schema fact rather
than application code. The ONLY thing that lives in memory is the ephemeral session
pin, which must never reach the user's database — otherwise a read-only viewing
invocation (``factory-console /some/other/project``, to glance at it) would mutate the
selection the operator set from the UI.

**A resolution that cannot establish its answer REFUSES.** Every failure below is a
named member of :data:`SelectionFailure` and a named error, never a fallback to the pin
or to "the first project". Substituting a different project would render one project's
tickets, run-state and spend under a heading naming another — the same silent
mis-answer :meth:`~factory_console.store.registry_protocol.ProjectRegistry.get_selected_project`
already forbids at the port.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final, Literal

from factory_console.errors import FactoryConsoleError
from factory_console.store.registry_protocol import ProjectRegistry

SESSION_PROJECT_ID: Final = "session"
"""The reserved id of the ephemeral, UNREGISTERED project a ``PATH`` boot pins.

It is a sentinel, not a row: nothing in the registry has this id, and
:meth:`SelectionState.select` refuses to persist it (see that method). Reserving it
is safe by construction rather than by convention — a real registry id is a bare
uuid4 hex matching
:data:`~factory_console.domain.registry.REGISTERED_PROJECT_ID_PATTERN` (32 lowercase
hex digits), which ``"session"`` cannot be, so no registered project can ever collide
with it and no lookup for it can ever accidentally succeed.
"""

SelectionFailure = Literal[
    "no_selection",
    "selected_project_not_registered",
    "selected_project_missing",
    "selected_project_unreadable",
]
"""Why the console cannot currently serve a project — the vocabulary, declared ONCE.

Every consumer draws its spelling from this union rather than inventing one: the
registry endpoints' ``reason``, ``/health``'s ``selectionReason``, and the 409 codes
the read and write endpoints answer with. A second spelling of any member would be a
contract the frontend's label map never sees, which is exactly the drift
:data:`~factory_console.domain.registry.RegistryEntryCondition` is declared once for.

Note what is NOT here: "the store itself could not be read". That is
:class:`RegistryUnreadable`, a 503 about the CONSOLE's own health, not a named state
of the user's selection — see that class.
"""


class NoProjectSelected(FactoryConsoleError):
    """Nothing is selected and no path was pinned, so there is no project to serve.

    Status 409, not 404: the request is well-formed and the resource it names is not
    missing — the console simply has no project chosen yet, which is the ordinary
    state of a fresh install and is fixed by selecting one. A 404 would send the user
    hunting for a URL that was never wrong. ``details`` carries the
    :data:`SelectionFailure` member so a client branches on the machine-readable
    reason instead of matching on prose.

    Attributes:
        reason: The same :data:`SelectionFailure` member, typed, for the one caller
            that reports this condition instead of raising it —
            :mod:`factory_console.api.v1.health`, which answers ``200`` with a named
            ``selectionReason`` and must not dig it back out of an untyped
            ``details`` mapping.
    """

    def __init__(self) -> None:
        self.reason: SelectionFailure = "no_selection"
        super().__init__(
            code="no_project_selected",
            message="No project is selected; select one to view it.",
            status=409,
            details={"reason": self.reason},
        )


class SelectedProjectNotRegistered(FactoryConsoleError):
    """The selected id names no registry row — most often one removed mid-session.

    Reached when the in-process session selection outlives the row it names (the
    project was removed from the registry while it was being viewed), or when a
    persisted selection is read against a registry that no longer holds it. Status
    409 for the same reason as :class:`NoProjectSelected`: the console's state, not
    the request, is what needs fixing.

    Deliberately NOT a fallback to the pinned root or to another project. Falling
    back would answer a question about the project the user selected with a
    different project's data, under the selected project's name.

    Attributes:
        project_id: The selected id that no row answers to.
        reason: The :data:`SelectionFailure` member this error names, typed — see
            :class:`NoProjectSelected` for why both attributes exist beside
            ``details``.
    """

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.reason: SelectionFailure = "selected_project_not_registered"
        super().__init__(
            code="selected_project_not_registered",
            message=f"The selected project {project_id} is no longer registered.",
            status=409,
            details={"reason": self.reason, "projectId": project_id},
        )


class SelectedProjectUnavailable(FactoryConsoleError):
    """The selected project's path cannot be read right now.

    The row is intact and the selection is valid; the PATH is the problem — deleted,
    renamed, replaced by a file, or on a volume the console may not read. Status 409:
    a named, recoverable state of the console rather than a missing URL.

    The message names BOTH the path and WHICH failure it was, because the two send an
    operator to different places: ``selected_project_missing`` means find or re-add
    the directory, ``selected_project_unreadable`` means look at permissions or the
    mount. Collapsing them into one message would make the console assert "it is not
    there" about a directory it merely declined to look at — the same distinction
    :func:`~factory_console.file_adapter.project_condition.classify_project_path`
    refuses to blur. ``details`` repeats the reason machine-readably.

    Attributes:
        path: The selected project's path, which is known even though it cannot be
            read — ``/health`` still reports it, so an operator sees WHICH directory
            to go and look at.
        reason: The :data:`SelectionFailure` member this error names, typed — see
            :class:`NoProjectSelected` for why both attributes exist beside
            ``details``.
    """

    def __init__(self, path: Path, failure: SelectionFailure) -> None:
        self.path = path
        self.reason: SelectionFailure = failure
        what = "is missing" if failure == "selected_project_missing" else "could not be read"
        super().__init__(
            code="selected_project_unavailable",
            message=f"The selected project at {path} {what}.",
            status=409,
            details={"reason": failure, "path": str(path)},
        )


class RegistryUnreadable(FactoryConsoleError):
    """The console could not read its OWN store, so it cannot say what is selected.

    Status 503, and it is the only selection error that is not a 4xx: every other
    failure here is a true, stable statement about the user's projects that the user
    can act on, while this one says the console is currently unable to answer at all.
    A 409 would invite the user to fix a selection that may well be fine; a 503 says
    "retry, and check the console's own state directory".

    Not a member of :data:`SelectionFailure` for that same reason — that union names
    states of the SELECTION, and "I could not look" is not one of them.

    The message deliberately carries no filesystem path or OS detail — the caller
    logs the underlying error server-side (see
    :func:`~factory_console.api.deps._read_registry`); the client only needs to know
    to retry.
    """

    def __init__(self) -> None:
        super().__init__(
            code="registry_unreadable",
            message="The project registry could not be read.",
            status=503,
        )


class SelectionState:
    """The session's answer to "which project?", read through to the registry.

    Holds exactly one piece of durable-adjacent state of its own — the process-local
    ``session_selection`` — and delegates everything else to the
    :class:`~factory_console.store.registry_protocol.ProjectRegistry`. The pinned root
    is immutable for the life of the process: it is what ``factory-console PATH``
    discovered, and :attr:`pinned_root` keeps naming it even after the operator
    switches away, so ``app.state.project_root`` and this object never disagree.

    **NOT thread-safe, by design.** The console runs a single uvicorn worker on one
    event loop, and this object is mutated only from handler code on that loop. A lock
    would buy nothing but the false impression that concurrent mutation is supported;
    the blocking registry calls underneath are the ones that get offloaded to a worker
    thread, and they are the registry's own business (see
    :meth:`~factory_console.store.registry_protocol.ProjectRegistry.get_selected_project`),
    not this object's.

    **The one subscriber is the watcher supervisor.** The file watcher must be re-rooted
    when the selection moves, so :func:`factory_console.app._watcher_retarget_hook` wraps
    a :class:`~factory_console.services.watcher_supervisor.WatcherSupervisor` and
    ``create_app`` registers it through :meth:`subscribe`. It is the reason this hook
    exists, and — see :meth:`subscribe` — it deliberately never raises and never blocks
    the caller.
    """

    def __init__(
        self,
        *,
        pinned_root: Path | None,
        registry: ProjectRegistry | None,
    ) -> None:
        """Seed the session from the pinned root, if there is one.

        ``pinned_root`` is the root ``factory-console PATH`` discovered, or ``None``
        for a pathless boot. When it is present the session starts at
        :data:`SESSION_PROJECT_ID`, which is the whole precedence rule in one line: the
        typed path is the session's INITIAL selection, so the first request serves it
        even when the registry holds a persisted selection pointing elsewhere.

        ``registry`` may be ``None``, and that is a valid configuration rather than a
        wiring bug: it is "pinned mode", which is every pre-v3 app and every existing
        test. Such an app can never PERSIST a selection naming another project, because
        there is no registry to name it in — but :meth:`select` still moves the
        process-local selection for the life of the request that calls it, simply with
        nothing durable behind it (see :meth:`select`).
        """
        self.pinned_root = pinned_root
        self._registry = registry
        self._session_selection: str | None = (
            SESSION_PROJECT_ID if pinned_root is not None else None
        )
        self._on_change: list[Callable[[Path | None], None]] = []

    def current_id(self) -> str | None:
        """Return the selected project id, or ``None`` when nothing is selected.

        The process-local session selection wins when it is set — that is the pin
        taking effect at boot, and every later :meth:`select` taking effect
        immediately. Only when it is unset (a pathless boot that has not switched yet)
        does this READ THROUGH to the persisted selection, which is how a future
        ``serve`` invocation resumes where the operator left off.

        BLOCKING on that read-through path: the registry is ``sqlite3``. Callers on
        the event loop offload it with ``anyio.to_thread.run_sync`` exactly as they do
        every other port call — see
        :func:`~factory_console.api.deps.get_current_project_root`, which discharges
        the house rule once for all thirteen handler sites.
        """
        if self._session_selection is not None:
            return self._session_selection
        if self._registry is None:
            return None
        selected = self._registry.get_selected_project()
        return None if selected is None else selected.id

    def select(self, project_id: str | None) -> None:
        """Point the session at ``project_id``, persist it, and fire the hooks.

        Persistence is attempted FIRST — delegated to the registry, except for
        :data:`SESSION_PROJECT_ID`, which is never written. Only once that succeeds
        (or there is nothing to persist to) does the in-memory session selection
        move, so a rejected switch leaves this object exactly as it was: a raise
        below must never leave ``current_id()`` naming an id no row answers to.

        The :data:`SESSION_PROJECT_ID` exception to persistence is the point of the
        sentinel: the pinned root is an ephemeral property of one invocation and
        names no row, so persisting it would both violate the registry's foreign key
        and let a throwaway "just look at this directory" run overwrite the
        selection the operator made in the UI. With no registry bound there is
        nothing to persist to, and the selection is simply process-local.

        Raises:
            ProjectNotRegistered: ``project_id`` names no registry row. Raised BY the
                registry, deliberately not pre-empted here: the port already treats a
                selection of a non-existent project as the one write that must fail
                loudly rather than succeed at pointing the console at nothing. Nothing
                on this object is mutated when this is raised.
        """
        if self._registry is not None and project_id != SESSION_PROJECT_ID:
            self._registry.set_selected_project(project_id)
        self._session_selection = project_id
        root = self._resolve_root(project_id)
        for callback in self._on_change:
            callback(root)

    def subscribe(self, callback: Callable[[Path | None], None]) -> None:
        """Register ``callback`` to receive the newly selected root on every switch.

        Called with the resolved root, or ``None`` when the new selection resolves to
        no path at all (cleared, or an id whose row has gone). Subscribers are invoked in
        registration order, synchronously, inside :meth:`select`, on whatever thread
        called it — the event-loop thread for a request handler, a plain thread with no
        loop at all for a test or a future CLI-side switch — so a subscriber that raises
        WOULD fail the switch that provoked it, and one that blocks would block it.

        The one real subscriber is careful not to rely on that. ``select()`` is called
        from a request handler, and re-rooting the watcher both blocks (an observer join)
        and can fail (a watcher that will not build), so
        :func:`factory_console.app._watcher_retarget_hook` returns immediately and runs
        the swap as a task whose halves are documented never to raise. The deliberate
        consequence: losing live updates never turns a successful project switch into a
        failed request. Do not build an error path on a raising subscriber — nothing
        raises, and a switch is never failed by a lost watcher.
        """
        self._on_change.append(callback)

    def _resolve_root(self, project_id: str | None) -> Path | None:
        """Return the root ``project_id`` names, without probing the filesystem.

        The hook payload only, deliberately kept separate from
        :func:`~factory_console.api.deps.get_current_project_root`: a subscriber wants
        to know WHERE the selection now points, and answering ``None`` for a selection
        that resolves to nothing is a complete answer for it. Whether that path is
        readable is a per-request question the resolution seam asks, with the stat it
        must not perform here on every switch.
        """
        if project_id is None:
            return None
        if project_id == SESSION_PROJECT_ID:
            return self.pinned_root
        if self._registry is None:
            return None
        row = self._registry.get_project(project_id)
        return None if row is None else row.path
