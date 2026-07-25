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
"""

from __future__ import annotations

from fastapi import Request

from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.watcher import FileWatcher
from factory_console.file_adapter.writer_protocol import FileWriter


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
