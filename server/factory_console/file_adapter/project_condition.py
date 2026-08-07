# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""The :class:`ProjectConditionProbe` port — what is TRUE of a registered path, now.

A registry row (:class:`~factory_console.domain.registry.RegisteredProject`) is a
claim the USER made: "track a project at this path". Whether that claim still holds
is a question about the filesystem, and it has to be asked fresh on every read —
the path may have been deleted, renamed, made unreadable, or emptied of everything
that made it a project since the row was written. This module asks it, and answers
with exactly one member of
:data:`~factory_console.domain.registry.RegistryEntryCondition`, the union T103
fixed. The five names are NOT redefined here; they are imported, because the SPA's
label map derives from that one declaration and a second spelling of the vocabulary
would be a contract the frontend never sees.

**Why this is a PORT and not a bare function.** It is the argument
:mod:`~factory_console.file_adapter.run_artifacts` spells out at length, and it
applies here with nothing changed: a service that called a probe FUNCTION directly
would stat the host filesystem no matter which registry it was handed. Every
fake-backed test in this repo seeds paths that exist on no disk
(``Path("/proj")``, ``Path("/factory/demo-project")``), so such a service answers
``path_missing`` for every row while appearing to be under test — a blank produced
by the console's own wiring rather than by the filesystem, which is the exact
failure the v2.2 milestone existed to abolish. So the reads get a seam, and this
module holds :class:`ProjectConditionProbe`, :class:`RealProjectConditionProbe` and
:class:`FakeProjectConditionProbe` together, exactly as ``run_artifacts.py`` does.

**Why it lives in ``file_adapter/`` and not in ``store/``.** The store owns the
console's OWN database — rows the console wrote and may rewrite. This module reads
the TARGET PROJECT's files, which is precisely what the file-adapter layer owns and
what the store must not do (``PROJECT_STRUCTURE.md``, track ownership). Putting a
target-project stat behind the store would be the ownership story bending at the
first place it was inconvenient; it does not bend. Because it reads a project the
console does not own, it carries the literal READ-ONLY banner above and is pinned by
``tests/_read_only_guard.py``.

**The port is TOTAL.** No implementation ever raises for a source-level problem: a
missing path, a permission error, a symlink loop and an ordinary directory that was
never a project all come back as NAMED conditions. That totality is the whole
substance of the contract, because of where the answers are consumed — one per
registry row, composing a project switcher. A probe that raised on one bad row would
take out the WHOLE listing with a 500, deleting every healthy project from the user's
screen to report one deleted neighbour; and a probe that omitted the bad row would be
worse still, since a user who sees no row concludes they never registered it. Missing
must render AS missing (``ARCHITECTURE.md``, "Other factory artefacts").

Like :mod:`~factory_console.file_adapter.watcher` and
:mod:`~factory_console.file_adapter.run_artifacts`, this module is deliberately NOT
re-exported from ``file_adapter/__init__``; consumers import all three symbols by
full path, so adding this port touches no aggregation file.
"""

from __future__ import annotations

import os
import stat as stat_module
from pathlib import Path
from typing import Protocol, runtime_checkable

from factory_console.domain.registry import RegistryEntryCondition
from factory_console.file_adapter.discovery import MANIFEST_RELPATH

FACTORY_RELATIVE_DIR = Path(".factory")
"""The factory's run-state directory, relative to a project root.

The one thing that separates ``ok`` from ``no_factory_dir``. Spelled here as the
DIRECTORY itself rather than derived from one of
:mod:`~factory_console.domain.watched_artifacts`' paths under it
(``RESULTS_RELATIVE_DIR.parent`` and friends): those constants exist to pin WHERE a
particular artefact lives, and reaching through one of them for its parent would make
this module's meaning depend on an artefact it does not read. What is asked here is
whether the factory has run against this project AT ALL, which is a question about the
directory and about nothing inside it.
"""


def classify_project_path(path: Path) -> RegistryEntryCondition:
    """Establish, from disk, which single condition holds of ``path``.

    Pure in the sense that matters: it takes a path, reads the filesystem, and
    returns a value. It holds no state, caches nothing, and — like everything behind
    :class:`ProjectConditionProbe` — NEVER raises for a source-level problem.

    Resolved MOST-DEGRADED-FIRST, the precedence
    :data:`~factory_console.domain.registry.RegistryEntryCondition` declares
    (``unreadable`` > ``path_missing`` > ``not_a_project`` > ``no_factory_dir`` >
    ``ok``). Each step below can only ever ANSWER a condition at least as degraded as
    the ones already ruled out, so the first answer reached is the leftmost that
    holds:

    1. :meth:`~pathlib.Path.stat` the path itself. :class:`FileNotFoundError` and
       :class:`NotADirectoryError` both mean nothing is there to look at —
       ``path_missing``. The second is not a special case of "it is a file": it is
       what a path whose PARENT component is a file raises (``/some/file.txt/sub``),
       which is a registered path that no longer names anything. Any OTHER
       :class:`OSError` — ``EACCES`` on an ancestor, ``ELOOP`` from a symlink loop, a
       hardware or network-filesystem error — means the console could not look, which
       is ``unreadable``.
    2. The path exists but is not a directory → ``not_a_project``. This is the
       ``S_ISDIR`` test on the stat already taken, not a second syscall.
    3. The tickets manifest, at :data:`~factory_console.file_adapter.discovery.MANIFEST_RELPATH`
       — imported, never re-spelled, so this module cannot come to disagree with
       discovery about what makes a directory a project. An :class:`OSError` here is
       ``unreadable``; a clean ``False`` is ``not_a_project``.
    4. ``.factory/`` — probed in its OWN ``try``, after step 3 has answered, so a
       directory whose manifest is cleanly absent reports ``not_a_project`` rather
       than borrowing an error raised by a later probe it never needed. Absent →
       ``no_factory_dir`` (an :class:`OSError`, again, ``unreadable``): a real, browsable project whose
       run-state, runs and spend are legitimately missing rather than zero. This is
       the ORDINARY state of a fresh clone, since ``.factory/`` is gitignored.
    5. Otherwise ``ok``.

    **A permission error is NEVER answered as the more permissive** ``not_a_project``.
    "I could not look" is not "I looked and it is not a project": the first sends an
    operator to their file modes, the second sends them hunting for a project that was
    there all along. That is why step 3 catches :class:`OSError` around the manifest
    probe instead of leaning on :meth:`~pathlib.Path.is_file` returning ``False``.
    ``is_file`` swallows exactly the errnos that MEAN absence (``ENOENT``, ``ENOTDIR``,
    ``ELOOP``, ``EBADF``) and re-raises everything else, ``EACCES`` first among them —
    so without that ``except`` a manifest inside an unsearchable directory would
    propagate, breaking the totality this port promises; and "fixing" that by reading
    its ``False`` as absence would assert a fact about the directory the console never
    established.
    """
    try:
        stat_result = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return "path_missing"
    except OSError:
        return "unreadable"

    if not stat_module.S_ISDIR(stat_result.st_mode):
        return "not_a_project"

    try:
        has_manifest = (path / MANIFEST_RELPATH).is_file()
    except OSError:
        return "unreadable"
    if not has_manifest:
        return "not_a_project"

    try:
        has_factory_dir = (path / FACTORY_RELATIVE_DIR).is_dir()
    except OSError:
        return "unreadable"
    return "ok" if has_factory_dir else "no_factory_dir"


@runtime_checkable
class ProjectConditionProbe(Protocol):
    """Read seam answering "what is true of this registered path right now?".

    One method, taking the registered path as
    :attr:`~factory_console.domain.registry.RegisteredProject.path` holds it. An
    implementation must NOT re-resolve that path — the row's own docstring forbids
    it, because re-resolving in a different working directory, or through a symlink
    that has since changed, silently probes a DIFFERENT project than the one the user
    registered and then reports the answer under the registered row.

    ``probe`` is TOTAL, and that is the contract's whole substance: a conforming
    implementation NEVER lets an exception escape, for any path, on any filesystem.
    Every source-level problem comes back as a named
    :data:`~factory_console.domain.registry.RegistryEntryCondition`, so a caller
    composing one entry per registry row cannot have a single deleted or unreadable
    neighbour fail the whole listing. An implementation that raises is not a
    conforming ``ProjectConditionProbe``.

    ``unreadable`` and ``not_a_project`` are NOT interchangeable, per that union: an
    implementation that answers ``not_a_project`` when it merely declined to look
    asserts a fact about the user's disk it did not establish.
    """

    def probe(self, path: Path) -> RegistryEntryCondition:
        """Return the single condition that holds of ``path``, most-degraded-first."""
        ...


class RealProjectConditionProbe:
    """Filesystem-backed :class:`ProjectConditionProbe` over :func:`classify_project_path`.

    Stateless (``RealProjectConditionProbe()`` takes no arguments and caches nothing)
    and composed, not re-implemented: the precedence lives in the classifier, which
    stays directly testable without a port, and this class is only the seam that lets
    a service be handed something else.

    It caches nothing ON PURPOSE. A registry listing asks this question once per row
    per read, and a memoised answer is a claim about the filesystem that goes stale
    the moment a file moves — the very staleness
    :class:`~factory_console.domain.registry.RegisteredProject` refuses to persist. If
    the syscall cost ever matters, bound it at the CALLER, where the request's
    lifetime is known.

    The calls are BLOCKING, as every file-adapter read in this repo is; the backend
    offloads them with ``anyio.to_thread.run_sync`` at the API boundary
    (``ARCHITECTURE.md``, Cross-cutting Concurrency), which is not this layer's
    business to arrange.
    """

    def probe(self, path: Path) -> RegistryEntryCondition:
        """Establish ``path``'s condition from disk."""
        return classify_project_path(path)


class FakeProjectConditionProbe:
    """In-memory :class:`ProjectConditionProbe` for deterministic tests.

    Satisfies the Protocol structurally (no inheritance);
    ``isinstance(fake, ProjectConditionProbe)`` holds because it is
    ``@runtime_checkable``. Seeded with ``{path: condition}``, it touches NO
    filesystem — which is the entire point, and the reason this port exists at all: it
    lets a registry service be exercised against paths in whatever condition a test
    needs, including paths that exist on no disk, without the real probe answering
    ``path_missing`` for every one of them.

    An unseeded path answers ``default``, which defaults to ``ok``. That default is a
    deliberate convenience rather than an imitation of the real probe — the real probe
    would answer ``path_missing`` for a synthetic ``/proj`` — because the overwhelmingly
    common fake-backed test seeds a healthy registry and cares about something else
    entirely. A test about degradation states its degradation explicitly, either in
    ``conditions`` or by constructing the fake with a different ``default``.

    **Lookup keys are normalized PURELY, without touching disk.**
    :meth:`~pathlib.Path.resolve` is the obvious canonicaliser and is exactly wrong
    here: it stats the filesystem, which is the one thing this class exists not to do,
    and on a seeded ``/factory/demo-project`` it has nothing to resolve against
    anyway. So both the seeded keys and the probed path go through
    :func:`os.path.normpath`, which is pure string algebra: it collapses redundant
    separators, drops ``.`` components, strips a trailing separator and folds ``..``
    lexically. A test may therefore spell a path ``/a/b``, ``/a/b/`` or ``/a/./b`` and
    get the same answer, which is the property that matters — a fake that missed on a
    trailing slash would send a test hunting for a bug in the code under test.

    The ``..`` folding is LEXICAL and so differs from ``resolve()`` where a component
    is a symlink (``/a/link/../b``). That difference is accepted, stated rather than
    hidden: deciding it honestly requires the I/O this class refuses, and a fake's
    seeded paths are synthetic ones with no symlinks to be wrong about.
    """

    def __init__(
        self,
        conditions: dict[Path, RegistryEntryCondition] | None = None,
        default: RegistryEntryCondition = "ok",
    ) -> None:
        """Seed per-path conditions; unseeded paths answer ``default``."""
        seeded = {} if conditions is None else conditions
        self._conditions = {self._key(path): condition for path, condition in seeded.items()}
        self._default = default

    def probe(self, path: Path) -> RegistryEntryCondition:
        """Return the seeded condition for ``path``, or the default, with no I/O."""
        return self._conditions.get(self._key(path), self._default)

    @staticmethod
    def _key(path: Path) -> str:
        """Normalize ``path`` to its lookup key by pure string algebra — see the class docstring."""
        return os.path.normpath(path)
