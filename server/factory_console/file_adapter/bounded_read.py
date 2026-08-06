# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""ONE open/fstat/``S_ISREG``/cap/bounded-read sequence, shared by every reader
under ``.factory/``.

:mod:`~factory_console.file_adapter.ledger` and
:mod:`~factory_console.file_adapter.runs` each held an independent copy of this
exact sequence, under a docstring in each promising to keep the two in step by
hand — the same drift hazard :data:`~factory_console.file_adapter.path_safety.
ABSENT_ERRNOS` was consolidated to remove, one layer up (see that constant's own
comment for the fuller history). The hazard here is not hypothetical: this
sequence exists BECAUSE a ``stat``-then-``open`` of the same NAME let a FIFO
substituted in between stat as ``st_size == 0``, sail past a byte cap, and block
an ``async`` handler's event loop forever — hanging every route in the app. A
tightening applied to one copy and not the other would leave one artifact reader
hardened and its sibling exposed, with nothing to say which is which. See T97.

Every gate — node type, size, byte bound — is applied to the OPENED DESCRIPTOR
rather than to a path. ``.factory/`` is written by a process the console does not
control, so a ``stat`` and a later ``open`` of the same name are two independent
lookups with a swap in between, and ``os.fstat`` cannot describe a different file
from the one the bytes come from. ``O_NONBLOCK`` makes the open itself total (a
FIFO returns instead of blocking forever), ``O_NOFOLLOW`` refuses a symlink
swapped in as the final path component, and both degrade to ``0`` via ``getattr``
on a platform that lacks them.

Callers differ in what "not found" MEANS to them: :mod:`~factory_console.
file_adapter.runs` treats a missing artifact as the ordinary state of a fresh
clone and logs nothing, while :mod:`~factory_console.file_adapter.ledger` only
calls this once :func:`~factory_console.file_adapter.ledger.find_ledger_path` has
already proven the ledger exists, so reaching "not found" here is a race worth a
warning. This module does not choose between them: ``"not_found"`` is reported
and never logged here, leaving that call to the caller.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_LOGGER = logging.getLogger(__name__)

BoundedReadOutcome = Literal["ok", "not_found", "unreadable", "too_large"]
"""What :func:`read_bounded` decided about the path.

- ``"ok"`` — ``data`` holds the file's bytes, at most ``max_bytes``.
- ``"not_found"`` — no file exists at the path (or a path component is not a
  directory). NEVER logged here; see the module docstring for why.
- ``"unreadable"`` — the file exists (or its absence could not be proven) but its
  bytes could not be read: open/stat/read failed for a reason other than
  absence, or the node is not a regular file (FIFO, directory, socket, device).
- ``"too_large"`` — the file is over ``max_bytes`` and was not read, or grew past
  the cap while being read. Never short-read.
"""


@dataclass(frozen=True)
class BoundedRead:
    """The result of :func:`read_bounded`: the bytes, or which gate refused them.

    ``data`` is ``b""`` on every outcome but ``"ok"``.
    """

    outcome: BoundedReadOutcome
    data: bytes = b""


def read_bounded(path: Path, *, max_bytes: int, label: str) -> BoundedRead:
    """Read at most ``max_bytes`` (+1, to detect growth) from ``path``.

    NEVER raises. ``label`` names the caller in every log line this function
    emits — ``"%s: %s ..."``, never ``%r`` on the path, so the same path never
    reads two ways across the console's logs.

    ``max_bytes`` bounds the READ itself, not merely a preceding ``fstat``: the
    files under ``.factory/`` are rewritten by a process the console does not
    control, so a size observed before the read is already stale by the time it
    is acted on. Reading ``max_bytes + 1`` makes the cap a property of this call:
    at most one byte over is ever held, and that byte is what tells "exactly at
    the cap" (read) apart from "over it" (reported as ``"too_large"``).
    """
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, NotADirectoryError):
        return BoundedRead(outcome="not_found")
    except OSError as error:
        _LOGGER.warning("%s: %s could not be opened: %r", label, path, error)
        return BoundedRead(outcome="unreadable")
    except ValueError:
        # A path that cannot be encoded (an embedded NUL). ``os.open`` raises this
        # rather than an OSError, so the clause above does not cover it.
        _LOGGER.warning("%s: a path could not be encoded; it is not read", label)
        return BoundedRead(outcome="unreadable")

    try:
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            _LOGGER.warning("%s: %s could not be stat'd: %r", label, path, error)
            return BoundedRead(outcome="unreadable")

        if not stat.S_ISREG(info.st_mode):
            # Asked of the OPENED file, so a later swap cannot invalidate the
            # answer. A size bounds a regular file and nothing else — a FIFO and a
            # character device both stat as ``st_size == 0`` and would sail past
            # the cap below. This is also the ONLY thing that settles the
            # DIRECTORY case: ``O_RDONLY`` does not fail on a directory (``EISDIR``
            # is for ``O_WRONLY``/``O_RDWR``), so do not remove this as redundant
            # with the open.
            _LOGGER.warning("%s: %s is not a regular file; it is not read", label, path)
            return BoundedRead(outcome="unreadable")

        if info.st_size > max_bytes:
            _LOGGER.warning(
                "%s: %s is %d bytes, over the %d-byte cap; not read",
                label,
                path,
                info.st_size,
                max_bytes,
            )
            return BoundedRead(outcome="too_large")

        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(max_bytes + 1)
        except OSError as error:
            # Note absence is not reachable here: the file is open, so it cannot
            # vanish mid-read — unlinking drops the name, not the inode.
            _LOGGER.warning("%s: %s could not be read: %r", label, path, error)
            return BoundedRead(outcome="unreadable")
    finally:
        # ``closefd=False`` above so this one owner closes the descriptor on every
        # path, including the early returns that never reach ``fdopen``. Guarded,
        # because raising from a ``finally`` would replace the outcome already
        # computed, and a failed close cannot change the answer, only lose the
        # descriptor.
        try:
            os.close(descriptor)
        except OSError as error:
            _LOGGER.warning("%s: %s could not be closed: %r", label, path, error)

    if len(raw) > max_bytes:
        _LOGGER.warning(
            "%s: %s grew past the %d-byte cap while being read; not read",
            label,
            path,
            max_bytes,
        )
        return BoundedRead(outcome="too_large")

    return BoundedRead(outcome="ok", data=raw)
