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
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path as PathParam
from fastapi import Request

from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.run_artifacts import RunArtifactReader
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.file_adapter.writer_protocol import FileWriter

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
