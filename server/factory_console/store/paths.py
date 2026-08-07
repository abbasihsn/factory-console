"""The canonical-path rule for registered projects, and its named error.

A registry row's identity IS its path, so the registry needs ONE spelling of a
directory and every caller has to arrive at the same one.
:func:`canonical_project_path` is that single rule, and it ships beside the
:mod:`~factory_console.store.registry_protocol` port rather than inside an
implementation because it is a clause of the CONTRACT: the port promises
:attr:`~factory_console.domain.registry.RegisteredProject.path` is always
canonical, so a consumer never re-resolves it (see that field's docstring) and no
two implementations can disagree about whether two spellings name one project.

**Canonical = :meth:`~pathlib.Path.expanduser` then
``resolve(strict=False)``, stored absolute.** The parts, and why each is there:

- ``resolve()`` is what collapses ``~/dev/foo``, ``/Users/me/dev/foo``, a
  symlinked alias of either, and ``/Users/me/dev/../dev/foo/`` into ONE row. It
  is what makes the ``UNIQUE`` index on ``projects.path`` mean "one project",
  rather than "one way of typing a project".
- **``strict=False`` is required, not incidental.** The store must be able to
  hold — and later read back — a row whose path has since been deleted or whose
  volume was unmounted, because that row is still a true record of what the user
  asked the console to track. A strict resolve would raise at exactly the moment
  :data:`~factory_console.domain.registry.RegistryEntryCondition` has the
  vocabulary (``path_missing``, ``unreadable``) to answer honestly, turning a
  named, renderable condition into a failed request.
- A RELATIVE path is REFUSED, not resolved. Resolving it would silently address
  it against the server process's working directory — something the caller
  (a browser, on the other side of an HTTP boundary) cannot see and did not
  choose, so the row would name a directory nobody asked for.
- A project NESTED inside another registered project is explicitly ALLOWED. A
  monorepo can legitimately hold two App Factory projects, and the console has no
  basis for refusing the second; this is stated so it reads as a decision rather
  than as an omission somebody later "fixes".

Note what canonicalisation deliberately does NOT do: it never touches the
filesystem's *contents*. It does not check that the path exists, is a directory,
is readable, or is an App Factory project. Those are read-time observations the
condition union above names per-request; baking any of them into the write path
would make registration fail for a laptop with an external drive currently
unplugged.
"""

from __future__ import annotations

from pathlib import Path

from factory_console.errors import FactoryConsoleError

_BLANK_REASON = "Project path must not be blank"
_RELATIVE_REASON = (
    "Project path must be absolute; a relative path would resolve against the "
    "server's working directory, which the caller cannot see"
)
_UNRESOLVABLE_REASON = "Project path could not be resolved"


class InvalidProjectPath(FactoryConsoleError):
    """A path cannot be canonicalised into a registry row's identity.

    Raised by :func:`canonical_project_path` for a blank path, a path that is
    still relative after ``~`` expansion, or one whose resolution itself fails.
    Every cause maps to the same transport contract (status 400,
    ``invalid_project_path``) so the edge layer rejects an unusable path
    uniformly, and ``reason`` carries the human-readable difference — the stable
    ``code`` never varies. Co-located with the rule it guards, exactly as
    :class:`~factory_console.file_adapter.path_safety.PathTraversal` is.

    ``details`` echoes the path **as given**, before expansion or resolution.
    That is the caller's own input, so returning it discloses nothing they do not
    already have, and it is the only form they can act on; the RESOLVED path is
    deliberately never echoed, since it would leak the server's filesystem layout
    (the same rule ``PathTraversal`` follows for ticket ids).
    """

    def __init__(self, path: Path | str, *, reason: str) -> None:
        super().__init__(
            code="invalid_project_path",
            message=reason,
            status=400,
            details={"path": str(path)},
        )


def canonical_project_path(raw: Path | str) -> Path:
    """Return the one canonical absolute :class:`~pathlib.Path` for ``raw``.

    The single implementation of the identity rule documented at module level:
    reject blank, expand ``~``, reject a still-relative path, then
    ``resolve(strict=False)``. Everything that writes or looks up a registry row
    goes through here, so ``~/dev/foo`` and ``/Users/me/dev/foo`` are one project
    no matter which spelling reaches the console first.

    Raises:
        InvalidProjectPath: ``raw`` is blank or whitespace-only; is still
            relative after ``~`` expansion; or could not be resolved at all.

    Blankness is tested on the STRING, before :class:`~pathlib.Path` sees it,
    because ``Path("")`` is ``Path(".")`` — an empty path would otherwise slip
    through as the server's working directory, which is precisely the
    mis-addressing the relative-path rule exists to prevent.

    Neither ``expanduser()`` nor ``resolve()`` is total, and non-strict does not
    mean non-raising: ``~someone-with-no-account`` raises :class:`RuntimeError`
    from the former, and through CPython 3.12 a symlink loop raises
    :class:`RuntimeError` from the latter while 3.13 answers the unresolved path
    (the interpreter drift
    :func:`~factory_console.file_adapter.path_safety.resolve_or_none` documents,
    met here for a caller-supplied project path). ``pyproject.toml`` declares
    ``requires-python = ">=3.11"`` with no upper bound, so both behaviours are in
    the supported range. Unhandled, a path a user typed into the "add project"
    field would escape as an unmapped 500 from a function documented to raise
    only :class:`InvalidProjectPath`; it is reported as the 400 it is instead —
    "I cannot turn this into an identity" is a statement about the input, not a
    server fault. This is NOT the ``strict=False`` case: a path that merely does
    not exist resolves fine and registers fine, by design.
    """
    given = str(raw)
    if not given.strip():
        raise InvalidProjectPath(given, reason=_BLANK_REASON)

    try:
        expanded = Path(given).expanduser()
    except RuntimeError as exc:
        raise InvalidProjectPath(given, reason=_UNRESOLVABLE_REASON) from exc

    if not expanded.is_absolute():
        raise InvalidProjectPath(given, reason=_RELATIVE_REASON)

    try:
        return expanded.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise InvalidProjectPath(given, reason=_UNRESOLVABLE_REASON) from exc


def default_project_name(path: Path) -> str:
    """Return the display name to use for ``path`` when the user gave none.

    The canonical path's final component — ``/Users/me/dev/foo`` becomes
    ``foo`` — falling back to the path's string form when it has no final
    component, which is the filesystem root (``Path("/").name`` is ``""``). The
    fallback is not decorative: a
    :class:`~factory_console.domain.registry.RegisteredProject` requires a
    non-empty ``name``, so returning ``""`` for a root would make the model
    reject a row the store had already decided to write.

    A DEFAULT only. The stored name is the user's label and is never re-derived
    from the path afterwards (see ``RegisteredProject.name``): renaming the
    directory must not silently rename the project in the switcher.
    """
    return path.name or str(path)
