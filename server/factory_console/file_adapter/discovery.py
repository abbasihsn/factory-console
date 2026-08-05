"""Git-style project discovery: an explicit path wins, else an upward walk.

The CLI passes an optional ``PATH`` and the current working directory here. When
``PATH`` is given it is used verbatim if it holds the tickets manifest; otherwise
we walk ``cwd`` and its ancestors until a directory containing the manifest is
found. Isolating this from CLI/HTTP concerns keeps callers a thin shell and lets
the walk be exercised deterministically against ``tmp_path`` in unit tests.
"""

from __future__ import annotations

from pathlib import Path

from factory_console.errors import FactoryConsoleError

MANIFEST_RELPATH = Path("docs/planning/tickets.json")
"""Where a project's manifest lives, relative to its root.

Public because it is no longer only discovery's business: ``real._ROADMAP_RELPATHS``
derives the roadmap's primary location from this constant's parent, so the two
cannot drift into disagreeing about where a project's planning directory is.
"""


class ProjectNotFound(FactoryConsoleError):
    """No App Factory project (a directory holding the tickets manifest) was found.

    Mapped to CLI exit code 1 and HTTP 404 by the edge layers. ``starting_dir`` is
    the directory discovery began from (the resolved walk origin, or the explicit
    path the caller supplied) and is retained for callers that want to report it.
    """

    def __init__(self, starting_dir: Path) -> None:
        super().__init__(
            code="project_not_found",
            message=(
                f"No App Factory project found for {starting_dir}: missing {MANIFEST_RELPATH}."
            ),
            status=404,
        )
        self.starting_dir = starting_dir


def find_project_root(start: Path) -> Path:
    """Walk ``start`` and its ancestors, returning the first that holds the manifest.

    ``start`` is resolved with ``strict=False`` first so symlinks are followed and a
    non-existent tail still yields a usable path. The resolved directory is checked
    before its parents; if no candidate up to the filesystem root holds the manifest,
    :class:`ProjectNotFound` is raised naming the resolved origin.
    """
    resolved = start.resolve(strict=False)
    for candidate in (resolved, *resolved.parents):
        if (candidate / MANIFEST_RELPATH).is_file():
            return candidate
    raise ProjectNotFound(resolved)


def discover_project(explicit: Path | None, cwd: Path) -> Path:
    """Resolve the target project: an explicit path wins, else walk up from ``cwd``.

    When ``explicit`` is given it is returned as-is (unresolved) if it holds the
    manifest, else :class:`ProjectNotFound` is raised for it. When ``explicit`` is
    ``None`` the discovery falls back to :func:`find_project_root` from ``cwd``.
    """
    if explicit is not None:
        if (explicit / MANIFEST_RELPATH).is_file():
            return explicit
        raise ProjectNotFound(explicit)
    return find_project_root(cwd)
