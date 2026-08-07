"""FastAPI dependency providers for the HTTP edge.

:func:`get_file_adapter` is the single DI seam every handler uses to obtain the
request-scoped :class:`~factory_console.file_adapter.protocol.FileAdapter`
without importing a concrete adapter (never ``real.py``). ``create_app`` stashes
the adapter on ``app.state.file_adapter`` at boot; this provider reads it back so
handlers stay decoupled from both the wiring and the filesystem implementation.

:func:`get_file_watcher` is the companion seam for the optional long-lived
:class:`~factory_console.file_adapter.watcher.FileWatcher` (T39): it returns the
watcher ``create_app`` bound on ``app.state.file_watcher``, or ``None`` when the
app was built without one — so the SSE endpoint (T45) degrades gracefully rather
than 500ing, unlike :func:`get_file_adapter` (a missing adapter is a wiring bug).

:func:`get_file_writer` is the write-side twin of :func:`get_file_adapter`: it
returns the write-core
:class:`~factory_console.file_adapter.writer_protocol.FileWriter` that ``create_app``
bound on ``app.state.file_writer``, so the v2 write endpoints obtain the writer
through ``Depends(get_file_writer)`` without importing a concrete writer. Like the
adapter seam (and unlike the opt-in watcher), a missing writer is a wiring bug, so
the provider raises rather than returning ``None``.

:func:`get_run_artifact_reader` is the same seam for T88/T89's
:class:`~factory_console.file_adapter.run_artifacts.RunArtifactReader` — the small
read port for the per-ticket lane artifacts that ``FileAdapter`` deliberately does
not carry — which ``create_app`` binds on ``app.state.run_artifact_reader`` for the
runs endpoint. Like the adapter and the writer, a missing reader is a wiring bug it
raises on, not a configuration the endpoint degrades over: the port is TOTAL, so an
absent artifact already has a named answer, and there is nothing left for a ``None``
reader to mean.

:func:`get_project_registry`, :func:`get_selection_state` and
:func:`get_current_project_root` are v3.0's selection seam. The first two read what
``create_app`` bound on ``app.state``; the third is the one that matters — the single
place that answers "which project root is THIS request about?", so the thirteen
handler sites stop re-deriving it from the boot-time pin. The precedence it
implements, and the named vocabulary it fails with, are documented in
:mod:`factory_console.services.project_selection`; this module only performs the
resolution and the blocking-call offload it needs.
"""

from __future__ import annotations

import logging
import os
import stat as stat_module
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Annotated, TypeVar

import anyio.to_thread
from fastapi import Path as PathParam
from fastapi import Request

from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.file_adapter.path_safety import ABSENT_ERRNOS
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.run_artifacts import RunArtifactReader
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.file_adapter.writer_protocol import FileWriter
from factory_console.services.project_selection import (
    SESSION_PROJECT_ID,
    NoProjectSelected,
    RegistryUnreadable,
    SelectedProjectNotRegistered,
    SelectedProjectUnavailable,
    SelectionFailure,
    SelectionState,
)
from factory_console.store.registry_protocol import ProjectRegistry

_LOGGER = logging.getLogger(__name__)

_ReadResult = TypeVar("_ReadResult")

TicketIdPath = Annotated[str, PathParam(pattern=TICKET_ID_PATTERN)]
"""A ``{ticket_id}`` path parameter validated at the FastAPI boundary.

Shared by the read routes (``api/v1/tickets.py``) and the write routes
(``api/v1/tickets_write.py``) so how a ticket id is constrained at the HTTP edge is
stated ONCE: an invalid id becomes the ``invalid_ticket_id`` 400 envelope and never
reaches the adapter or the writer. Lives here rather than in
:mod:`factory_console.domain.ticket` because it is FastAPI-specific wiring, and the
domain layer must not import a web framework — the domain still owns the pattern
itself via :data:`TICKET_ID_PATTERN`.
"""


def get_file_adapter(request: Request) -> FileAdapter:
    """Return the :class:`FileAdapter` bound to the app at boot.

    Reads ``request.app.state.file_adapter``, which ``create_app`` sets from its
    ``file_adapter`` argument, and is the target of ``Depends(get_file_adapter)``
    in every handler. Raises :class:`RuntimeError` when the adapter is unbound or
    ``None`` — a programmer error meaning the app was built without the DI seam
    wired, never a client-triggerable condition.
    """
    adapter = getattr(request.app.state, "file_adapter", None)
    if adapter is None:
        raise RuntimeError(
            "No FileAdapter bound on app.state.file_adapter; "
            "build the app with create_app(file_adapter=...)."
        )
    return adapter


def get_file_watcher(request: Request) -> FileWatcher | None:
    """Return the :class:`FileWatcher` bound to the app at boot, or ``None``.

    Reads ``request.app.state.file_watcher``, which ``create_app`` sets from its
    optional ``file_watcher`` argument, and is the target of
    ``Depends(get_file_watcher)`` in the SSE endpoint. Returns ``None`` (never
    raises) when no watcher was wired — the watcher is opt-in, so an app built
    without one is a valid configuration the consumer degrades over, not a bug.
    """
    return getattr(request.app.state, "file_watcher", None)


def get_file_writer(request: Request) -> FileWriter:
    """Return the :class:`FileWriter` bound to the app at boot.

    Reads ``request.app.state.file_writer``, which ``create_app`` sets from its
    ``file_writer`` argument, and is the target of ``Depends(get_file_writer)`` in
    every write handler. Raises :class:`RuntimeError` when the writer is unbound or
    ``None`` — a programmer error meaning the app was built without the write DI
    seam wired, never a client-triggerable condition (exactly like
    :func:`get_file_adapter`, not the opt-in :func:`get_file_watcher`).
    """
    writer = getattr(request.app.state, "file_writer", None)
    if writer is None:
        raise RuntimeError(
            "No FileWriter bound on app.state.file_writer; "
            "build the app with create_app(file_writer=...)."
        )
    return writer


def get_run_artifact_reader(request: Request) -> RunArtifactReader:
    """Return the :class:`RunArtifactReader` bound to the app at boot.

    Reads ``request.app.state.run_artifact_reader``, which ``create_app`` sets from
    its ``run_artifact_reader`` argument, and is the target of
    ``Depends(get_run_artifact_reader)`` in the runs endpoint. Raises
    :class:`RuntimeError` when the reader is unbound or ``None`` — a programmer error
    meaning the app was built without the artifact-read seam wired, exactly like
    :func:`get_file_adapter` and :func:`get_file_writer` and unlike the opt-in
    :func:`get_file_watcher`. Returning ``None`` here would be worse than raising: the
    endpoint's only honest fallback would be to report every artifact as unread, which
    is a claim about the FACTORY made from a fact about the console's own wiring.
    """
    reader = getattr(request.app.state, "run_artifact_reader", None)
    if reader is None:
        raise RuntimeError(
            "No RunArtifactReader bound on app.state.run_artifact_reader; "
            "build the app with create_app(run_artifact_reader=...)."
        )
    return reader


def get_project_registry(request: Request) -> ProjectRegistry | None:
    """Return the :class:`ProjectRegistry` bound to the app at boot, or ``None``.

    Reads ``request.app.state.project_registry``, which ``create_app`` sets from its
    optional ``project_registry`` argument. Returns ``None`` (never raises) when no
    registry was wired, degrading like :func:`get_file_watcher` rather than raising
    like :func:`get_file_adapter` — and that difference is load-bearing rather than
    stylistic. A registry-less app is PINNED MODE: it serves exactly the one root
    ``factory-console PATH`` discovered, which is every pre-v3 app, every existing
    test, and the behaviour v3.0 promises not to change. Raising here would make the
    single valid configuration this milestone must preserve look like a wiring bug.
    """
    return getattr(request.app.state, "project_registry", None)


def get_selection_state(request: Request) -> SelectionState:
    """Return the :class:`SelectionState` ``create_app`` built at boot.

    Reads ``request.app.state.selection``. Raises :class:`RuntimeError` when it is
    unbound — unlike :func:`get_project_registry` beside it, this is never a valid
    configuration: ``create_app`` ALWAYS constructs one (a registry-less app simply
    gets a permanently pinned selection), so an absent one means the app was not built
    by ``create_app`` at all. There is no honest ``None`` answer either, since the
    caller's next question is "which project?", and inventing one is precisely the
    fallback :mod:`factory_console.services.project_selection` refuses.
    """
    selection = getattr(request.app.state, "selection", None)
    if selection is None:
        raise RuntimeError(
            "No SelectionState bound on app.state.selection; "
            "build the app with create_app(...), which always constructs one."
        )
    return selection


def _probe_root(path: Path) -> SelectionFailure | None:
    """Return why ``path`` cannot be served right now, or ``None`` when it can.

    A deliberately NARROWER probe than
    :func:`~factory_console.file_adapter.project_condition.classify_project_path`, and
    not a reuse of it. That one answers a five-way
    :data:`~factory_console.domain.registry.RegistryEntryCondition` for the project
    SWITCHER, where "a real directory that is not a project" and "a project the
    factory has never run against" are useful things to show a user picking a row.
    This one answers a request that is already committed to a project, where the only
    question is whether the console can read the directory at all — the manifest and
    ``.factory/`` checks are then the endpoints' own business, with their own existing
    errors. Three answers, so: present-directory, missing, unreadable.

    The errno classification IS reused, via
    :data:`~factory_console.file_adapter.path_safety.ABSENT_ERRNOS`, because getting it
    wrong has the same consequence in both places: a permission error must never be
    reported as absence, or an operator is sent hunting for a directory that was there
    the whole time. ``ABSENT_ERRNOS`` is narrower than
    :meth:`~pathlib.Path.is_dir`'s own swallowing (which varies across 3.12/3.13), so
    a symlink loop stays ``unreadable`` on every interpreter.

    Two calls, because one cannot answer both halves. The :meth:`~pathlib.Path.stat`
    settles existence and directory-ness — a path replaced by a regular file is
    ``missing``, since no project directory is there. The :func:`os.scandir` then
    settles READABILITY, which the stat cannot: a ``chmod 000`` directory stats
    perfectly well from a traversable parent, and only an attempt to look INSIDE it
    raises ``EACCES``. Exactly one entry is pulled, so the cost does not grow with the
    project.
    """
    try:
        stat_result = path.stat()
    except OSError as error:
        if error.errno in ABSENT_ERRNOS:
            return "selected_project_missing"
        _LOGGER.warning("selected project: %s could not be stat'd: %r", path, error)
        return "selected_project_unreadable"

    if not stat_module.S_ISDIR(stat_result.st_mode):
        return "selected_project_missing"

    try:
        with os.scandir(path) as entries:
            next(entries, None)
    except OSError as error:
        if error.errno in ABSENT_ERRNOS:
            return "selected_project_missing"
        _LOGGER.warning("selected project: %s could not be read: %r", path, error)
        return "selected_project_unreadable"
    return None


async def get_current_project_root(request: Request) -> Path:
    """Resolve the project root THIS request is about — the v3.0 selection seam.

    Consumers write ``root: Path = Depends(get_current_project_root)`` and get the
    root of the SELECTED project instead of the one pinned at boot. The precedence,
    stated in full in :mod:`factory_console.services.project_selection`, is applied
    here in order:

    1. The session selection is :data:`SESSION_PROJECT_ID` and a pinned root exists →
       the pinned root, with no registry read and no stat. This is the rule that makes
       ``factory-console PATH`` serve the path the operator typed, even when the
       registry holds a persisted selection naming a different project.
    2. No selection at all → :class:`NoProjectSelected`.
    3. Otherwise the selection names a registry id, which is looked up through the
       port; an id no row answers to → :class:`SelectedProjectNotRegistered`.
    4. The row's path is probed; a path that is missing or unreadable →
       :class:`SelectedProjectUnavailable`, naming which.

    **Steps 3 and 4 NEVER fall back** — not to the pinned root, not to the first
    registered project, not to the previous selection. A resolution that could not
    establish its answer refuses, because the alternative is serving one project's
    tickets, run-state and spend under another project's name. That silent mis-answer
    is unfalsifiable from the UI, whereas a named 409 is a thing the user can fix.

    Both the registry read and the stat are BLOCKING (``sqlite3`` and syscalls), so
    both are awaited through ``anyio.to_thread.run_sync(partial(...))`` per
    ``ARCHITECTURE.md``'s Cross-cutting **Concurrency** rule. Discharging it HERE
    discharges it once for all thirteen handler sites instead of thirteen times.
    Nothing is cached: a fresh read per request, so a selection changed in another tab
    — or a project removed — is visible on the next request rather than at the next
    boot.

    Raises:
        NoProjectSelected: nothing is selected and no path was pinned.
        SelectedProjectNotRegistered: the selected id names no registry row.
        SelectedProjectUnavailable: the selected path is missing or unreadable.
        RegistryUnreadable: the console's own store could not be read.
    """
    selection = get_selection_state(request)
    registry = get_project_registry(request)

    # ``current_id`` reads through to the persisted selection when no session
    # selection is set, so it is a (potentially) blocking store read like any other.
    selected_id = await _read_registry(selection.current_id)

    if selected_id == SESSION_PROJECT_ID and selection.pinned_root is not None:
        return selection.pinned_root
    if selected_id is None or selected_id == SESSION_PROJECT_ID:
        # A session id without a pin is the pathless, never-switched boot: the
        # sentinel names a root that does not exist, which is "nothing selected".
        raise NoProjectSelected()
    if registry is None:
        # Pinned mode cannot name another project, so an id here means the selection
        # outlived the registry it came from. Refusing is the monotonic answer; the
        # pin is NOT substituted.
        raise SelectedProjectNotRegistered(selected_id)

    row = await _read_registry(partial(registry.get_project, selected_id))
    if row is None:
        raise SelectedProjectNotRegistered(selected_id)

    failure = await anyio.to_thread.run_sync(partial(_probe_root, row.path))
    if failure is not None:
        raise SelectedProjectUnavailable(row.path, failure)
    return row.path


async def _read_registry(read: Callable[[], _ReadResult]) -> _ReadResult:
    """Run a blocking registry read off the loop, naming an I/O failure as a 503.

    The offload is the house rule; the ``except`` is why this is a helper rather than
    two inline ``run_sync`` calls. An :class:`OSError` out of the store means the
    console could not reach its OWN database — a missing or unreadable state
    directory, a full or unmounted volume — which is a statement about the console's
    health, not about the user's selection. Left to propagate it would surface as a
    500 with no code; as :class:`RegistryUnreadable` it is a 503 that names the
    condition and tells the operator to look at the console's state directory rather
    than at their projects.
    """
    try:
        return await anyio.to_thread.run_sync(read)
    except OSError as error:
        raise RegistryUnreadable(str(error)) from error
