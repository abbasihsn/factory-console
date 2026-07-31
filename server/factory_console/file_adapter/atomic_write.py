# WRITE SITE: the ONLY sanctioned filesystem-mutating module in the console.
"""Apply a planned change-set to disk as one logical change (tmp-write + ``os.replace``).

This is the single sanctioned WRITE site in the whole console. Given the
:class:`~factory_console.file_adapter.write_render.PlannedChange` set that
:mod:`~factory_console.file_adapter.write_render` computed for a create/edit/delete
— the three coupled files ``docs/planning/tickets.json`` (manifest), the ticket
``<id>.md``, and ``ROADMAP.md`` — :func:`apply_changes` writes each file by
rendering its new text to a sibling temp file, ``fsync``-ing it, then
``os.replace``-ing it into place (atomic single-file swap on POSIX); a delete is an
unlink. Every change is guarded BEFORE any write, and — as an independent second
defense on top of write-render — the console HARD-REFUSES writing into the factory
run-state source, in EITHER of its forms: a marker directory or the factory's
``.factory/run-state.json``.

Concurrency (see ARCHITECTURE.md "Concurrency: single-worker Uvicorn. No locks"):
the console runs one Uvicorn worker and this is its only writer, so no file lock is
needed — no second writer can interleave. Atomicity is honest but bounded:
``os.replace`` gives PER-FILE atomicity (a reader sees either the old or the new
file, never a half-written one), and guarding every change up front means a bad
path aborts before any file is touched. It is NOT full multi-file
transactionality: a crash or an I/O failure PART-WAY through the sequence can leave
an earlier file already swapped and a later one not. The fixed apply order
(manifest → ``.md`` → roadmap) and the all-changes-first guard minimize the blast
radius, but a mid-sequence failure surfaces as :class:`AtomicWriteError` and the
caller should treat the trio as possibly inconsistent.
"""

from __future__ import annotations

import contextlib
import logging
import os
import stat
import tempfile
from pathlib import Path

from factory_console.domain.project import Project
from factory_console.domain.run_state_source import RUN_STATE_SOURCE_LOCATIONS
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.write_render import PlannedChange

_LOGGER = logging.getLogger(__name__)

_ESCAPES_ROOT_REASON = "Change target resolves outside the project root"
_RUN_STATE_REASON = "Change target resolves inside the factory run-state source"

# Manifest first, ticket .md second, roadmap last — a fixed, deterministic apply
# order so a partial failure always fails at a known boundary and the manifest
# (the reader's index of what exists) lands before the files it points at.
_MANIFEST_RANK = 0
_MD_RANK = 1
_ROADMAP_RANK = 2


class AtomicWriteError(FactoryConsoleError):
    """An I/O failure occurred while applying a planned change-set to disk.

    Raised (status 500) when a write, ``fsync``, ``os.replace``, ``mkdir``, or
    ``unlink`` fails mid-apply — a server-side filesystem problem, not bad input.
    The original :class:`OSError` is chained via ``raise ... from`` at the raise
    site. ``details`` carries only the project-relative ``relPath`` of the change
    being applied — never an absolute resolved path, which would disclose the
    server's filesystem layout.
    """

    def __init__(self, rel_path: str) -> None:
        super().__init__(
            code="atomic_write_failed",
            message=f"Failed to apply change to '{rel_path}'",
            status=500,
            details={"relPath": rel_path},
        )


def apply_changes(project: Project, planned: list[PlannedChange]) -> list[str]:
    """Apply ``planned`` to disk as one logical change; return the relPaths touched.

    Every change is guarded FIRST (containment under the project root + the
    run-state hard refusal) so an unsafe path aborts before any file is written.
    Then, in the fixed order manifest → ``.md`` → roadmap, each non-delete change is
    written via tmp-file + ``fsync`` + ``os.replace`` (creating parent dirs as
    needed) and each delete change unlinks its target (a missing target is fine).

    Returns the project-relative POSIX paths written or deleted, in apply order.

    Raises:
        PathTraversal: if any change resolves outside the project root OR inside the
            factory run-state directory — refused BEFORE any write. Both unsafe-path
            refusals share :class:`PathTraversal`'s ``invalid_ticket_id``/400
            contract so the edge layer rejects every unsafe write target with one
            ``except PathTraversal``; the run-state guard is a hard, independent
            second defense on top of write-render (which never emits a run-state
            path). The change's project-relative ``relPath`` is the identifier — the
            absolute resolved path never appears in the message or details.
        AtomicWriteError: if a filesystem operation fails mid-apply; any temp files
            this call created are cleaned up (best-effort) before it is re-raised.
    """
    ordered = sorted(planned, key=lambda change: _change_rank(project, change))
    _guard_all(project, ordered)

    created_temps: list[str] = []
    written: list[str] = []
    for change in ordered:
        try:
            if change.newText is None:
                _delete_if_present(change.path)
            else:
                _atomic_replace(change.path, change.newText, created_temps)
        except OSError as exc:
            _cleanup_temps(created_temps)
            _LOGGER.warning(
                "atomic apply failed mid-sequence; file set may be inconsistent",
                extra={"relPath": change.relPath, "written": written},
            )
            raise AtomicWriteError(change.relPath) from exc
        written.append(change.relPath)
    return written


def _change_rank(project: Project, change: PlannedChange) -> int:
    """Rank a change for deterministic apply order: manifest, then ``.md``, then roadmap.

    Classifies by resolved target path (both sides resolved for the symlinked
    ``/tmp`` -> ``/var/folders`` macOS case). Any change that is neither the manifest
    nor the roadmap is a ticket ``.md`` and sorts between them. ``sorted`` is stable,
    so changes sharing a rank keep their given order.
    """
    resolved = change.path.resolve(strict=False)
    if resolved == project.ticketsManifestPath.resolve(strict=False):
        return _MANIFEST_RANK
    if project.roadmapPath is not None and resolved == project.roadmapPath.resolve(strict=False):
        return _ROADMAP_RANK
    return _MD_RANK


def _guard_all(project: Project, changes: list[PlannedChange]) -> None:
    """Refuse the whole change-set before ANY write if any target is unsafe.

    For every change, resolve its absolute target and assert it is (a) contained
    under ``project.rootPath`` and (b) NOT inside any run-state artifact (either
    a marker directory or the factory's ``run-state.json``). Both sides are
    ``resolve(strict=False)``-d — matching write-render's symlinked-root idiom —
    so a symlinked temp root does not defeat :meth:`Path.is_relative_to`.
    """
    root = project.rootPath.resolve(strict=False)
    forbidden = _forbidden_run_state_paths(project)
    for change in changes:
        target = change.path.resolve(strict=False)
        if not target.is_relative_to(root):
            raise PathTraversal(change.relPath, reason=_ESCAPES_ROOT_REASON)
        if any(target.is_relative_to(run_state_dir) for run_state_dir in forbidden):
            raise PathTraversal(change.relPath, reason=_RUN_STATE_REASON)


def _forbidden_run_state_paths(project: Project) -> list[Path]:
    """Return every run-state artifact this writer must never write into (resolved).

    Covers BOTH artifact forms the console consumes read-only: every documented
    project-relative location
    (:data:`~factory_console.domain.run_state_source.RUN_STATE_SOURCE_LOCATIONS`
    — the marker DIRECTORIES *and* the factory's ``run-state.json``) under the
    project root, AND the concretely resolved ``project.runStateSource.path``
    (when set) — so the refusal holds whether or not discovery found the artifact.

    Derived from the FULL source tuple, NOT the directory-only
    :data:`~factory_console.file_adapter.run_state.RUN_STATE_RELATIVE_LOCATIONS`:
    a JSON-sourced project has ``runStateDir is None``, so a directory-only list
    named nothing at all for exactly the form the factory actually writes, leaving
    the highest-precedence run-state artifact unguarded. A directory entry forbids
    its whole subtree and a JSON entry forbids exactly that file — the
    :meth:`Path.is_relative_to` test in :func:`_guard_all` gives both, since a
    path is relative to itself.
    """
    forbidden = [
        (project.rootPath / relative).resolve(strict=False)
        for _kind, relative in RUN_STATE_SOURCE_LOCATIONS
    ]
    if project.runStateSource is not None:
        forbidden.append(project.runStateSource.path.resolve(strict=False))
    return forbidden


def _atomic_replace(target: Path, text: str, created_temps: list[str]) -> None:
    """Write ``text`` to ``target`` atomically via a sibling temp file + ``os.replace``.

    Creates the target's parent directory if needed, writes ``text`` (utf-8) to a
    :func:`tempfile.mkstemp` file in the SAME directory (so ``os.replace`` is a
    same-filesystem atomic rename), flushes and ``os.fsync``-s the fd for
    durability, then swaps it into place. The temp path is registered in
    ``created_temps`` while it exists on disk and removed once ``os.replace``
    consumes it, so a caller's cleanup unlinks only genuinely leftover temps.

    ``mkstemp`` always creates the temp ``0600`` and ``os.replace`` keeps the temp
    inode's mode, so the temp's mode is set to match the target BEFORE the swap — an
    existing target's own mode is preserved (an edit must not silently tighten a
    ``0644`` file to ``0600``), and a new target gets the mode a normal create would
    (``0666`` masked by the process umask).
    """
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=f".{target.name}.", suffix=".tmp")
    created_temps.append(tmp_name)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp_name, _mode_for_target(target))
    os.replace(tmp_name, target)
    created_temps.remove(tmp_name)


def _mode_for_target(target: Path) -> int:
    """Return the permission bits to apply to the file being swapped into ``target``.

    Preserve an existing target's own mode so an edit never changes its permissions;
    for a target that does not yet exist, use the mode a normal ``open(..., "w")``
    create would get — ``0o666`` masked by the process umask.
    """
    try:
        return stat.S_IMODE(os.stat(target).st_mode)
    except FileNotFoundError:
        umask = os.umask(0o022)
        os.umask(umask)
        return 0o666 & ~umask


def _delete_if_present(target: Path) -> None:
    """Unlink ``target`` if it exists; a missing target is not an error.

    A delete change (``newText is None``) removes the ticket ``.md``; the file being
    already absent is a benign no-op, not a failure.
    """
    with contextlib.suppress(FileNotFoundError):
        target.unlink()


def _cleanup_temps(created_temps: list[str]) -> None:
    """Best-effort unlink of every temp file still registered; swallow cleanup errors.

    Called on the failure path so a partial apply leaves no dangling ``mkstemp``
    file behind. Cleanup errors are swallowed — the original I/O failure is what the
    caller re-raises, and a failed unlink of a temp must not mask it.
    """
    for tmp_name in created_temps:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
