# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""The :class:`RunArtifactReader` port — per-ticket lane artifacts, behind a seam.

:mod:`~factory_console.file_adapter.runs` reads
``.factory/results/<id>.json`` and ``.factory/receipts/<id>.json`` as plain module
functions. Those functions are the file-adapter track's, and
:mod:`~factory_console.services.run_service` is the backend track's — which
"Depends only on domain models + FileAdapter Protocol" (``PROJECT_STRUCTURE.md``,
track ownership). A service that imported the readers directly would reach past
that seam into the ONLY layer allowed to call ``open()``, and would then read the
host filesystem no matter which adapter it was handed: every fake-backed test in
this repo seeds a ``Project`` whose ``rootPath`` does not exist
(``Path("/proj")``, ``Path("/factory/demo-project")``), so such a service answers
``absent`` for every source while appearing to be under test. That is exactly the
"blank field that could mean either 'the factory recorded nothing' or 'we did not
look'" this milestone exists to abolish, produced by the console's own wiring.

So the reads get a port. It is a SEPARATE, small ``Protocol`` rather than two more
methods on :class:`~factory_console.file_adapter.protocol.FileAdapter`, which is
how this repo has twice already added a capability the read port does not carry:
:class:`~factory_console.file_adapter.writer_protocol.FileWriter` for the write
path and :class:`~factory_console.file_adapter.watcher.FileWatcher` for the
long-lived watch. Neither appears in ``ARCHITECTURE.md``'s eight-method
``FileAdapter`` list, and neither forced every implementer of that list to grow
methods for a concern it does not have. This port follows them.

Two implementations satisfy it structurally: :class:`RealRunArtifactReader`, which
delegates to :mod:`~factory_console.file_adapter.runs` and owns the one degrade
those readers cannot make themselves, and :class:`FakeRunArtifactReader`, which
answers from a seeded map with no filesystem access at all. Like
:mod:`~factory_console.file_adapter.watcher`, this module is deliberately NOT
re-exported from ``file_adapter/__init__``; consumers import both symbols by full
path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from factory_console.domain import Project
from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.runs import (
    RECEIPTS_RELATIVE_DIR,
    RESULTS_RELATIVE_DIR,
    read_receipt,
    read_result,
    refusal_path,
)

_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class RunArtifactReader(Protocol):
    """Read seam for the factory's two per-ticket lane artifacts.

    Every method takes the resolved
    :class:`~factory_console.domain.project.Project` first, mirroring
    :class:`~factory_console.file_adapter.protocol.FileAdapter`'s shape.

    Both methods are TOTAL, and that is the contract's whole substance: a
    conforming implementation NEVER raises for a source-level problem. A missing,
    unreadable, malformed, oversized, or path-unsafe artifact comes back as an
    :class:`~factory_console.domain.runs.ArtifactRead` carrying a NAMED reason, so
    a caller composing one record per manifest ticket cannot have a single
    malformed neighbour fail the whole listing. An implementation that lets an
    exception escape for any of those is not a conforming ``RunArtifactReader``.

    ``absent`` and ``unreadable`` are NOT interchangeable, per
    :data:`~factory_console.domain.runs.ArtifactSkipReason`: an implementation
    that answers ``absent`` when it merely declined to look asserts a fact about
    the factory it did not establish, which is the collapse this port's callers
    exist to prevent.
    """

    def read_result(self, project: Project, ticket_id: str) -> ArtifactRead:
        """Return ``.factory/results/<ticket_id>.json``'s object, or WHY there is none."""
        ...

    def read_receipt(self, project: Project, ticket_id: str) -> ArtifactRead:
        """Return ``.factory/receipts/<ticket_id>.json``'s object, or WHY there is none."""
        ...


class RealRunArtifactReader:
    """Filesystem-backed :class:`RunArtifactReader` over :mod:`~factory_console.file_adapter.runs`.

    Stateless (``RealRunArtifactReader()`` takes no arguments and caches nothing)
    and composed, not re-implemented: it opens nothing itself. Its one job beyond
    delegation is to make the module functions' single raising case total, which is
    what the port promises and they do not.

    ``read_result``/``read_receipt`` are total over filesystem and content failures
    but NOT over the id: they re-validate it through
    :func:`~factory_console.file_adapter.path_safety.validate_ticket_id_as_segment`,
    which raises :class:`~factory_console.file_adapter.path_safety.PathTraversal`
    on TWO independent rules — a
    :data:`~factory_console.domain.ticket.TICKET_ID_PATTERN` violation, and a bare
    ``.`` or ``..``. Only the first is impossible from a manifest: the pattern is
    ``^[A-Za-z0-9_.-]+$``, which admits ``.`` as an ordinary character, so a
    manifest entry whose id is ``.`` or ``..`` builds a perfectly valid
    :class:`~factory_console.domain.ticket.TicketSummary` and then raises on the
    read.

    That raise is DEGRADED here rather than propagated, because this read happens
    once per MANIFEST ticket: letting it escape would fail the WHOLE listing with a
    400 naming an id the caller never supplied, deleting every healthy ticket's
    record to report one malformed neighbour. It is the same trade
    :meth:`~factory_console.file_adapter.real.RealFileAdapter._safe_run_state`
    makes for this id class on the ``list_tickets`` path — and, like that one, it is
    made HERE, inside the adapter layer, not in the service above the port. The
    single-ticket reads keep the hard guard; only the per-manifest LOOP degrades.

    The two are deliberately NOT factored into one shared helper, and this is the
    reason rather than an oversight: they agree on the shape (try, catch
    ``PathTraversal``, log once, return a degraded value) and on nothing else. They
    degrade to different TYPES — :attr:`~factory_console.domain.run_state.RunState.unreadable`
    there, an :class:`~factory_console.domain.runs.ArtifactRead` carrying a path
    here — from different inputs, under different log prefixes. A helper covering
    both would take the callable, the prefix AND a degraded-value factory, which is
    more machinery than either call site, and it would sit in a third module owning
    a policy neither layer could then read in one place. If a third such degrade
    ever appears, extract then.

    The reason is ``unreadable``, which is its established meaning: "the console
    refused to look at all", the branch
    :data:`~factory_console.domain.runs.ArtifactSkipReason` already names for a
    path that could not be proven safe. NOT ``absent`` — nothing here establishes
    that the factory wrote no artifact, and claiming it would be exactly the
    absent/unreadable collapse this milestone exists to remove.
    """

    def read_result(self, project: Project, ticket_id: str) -> ArtifactRead:
        """Read this ticket's result artifact, degrading a path-unsafe id."""
        return self._read(read_result, project.rootPath, RESULTS_RELATIVE_DIR, ticket_id)

    def read_receipt(self, project: Project, ticket_id: str) -> ArtifactRead:
        """Read this ticket's receipt artifact, degrading a path-unsafe id."""
        return self._read(read_receipt, project.rootPath, RECEIPTS_RELATIVE_DIR, ticket_id)

    @staticmethod
    def _read(
        reader: Callable[[Path, str], ArtifactRead],
        project_root: Path,
        relative_dir: Path,
        ticket_id: str,
    ) -> ArtifactRead:
        """Delegate to ``reader``, turning its one raising case into a named reason.

        The refusal is LOGGED rather than degraded in silence: unlike ``absent`` on
        a fresh clone, an id the console will not touch is a malformed manifest,
        which is a real condition an operator should see. The line names the
        ARTIFACT DIRECTORY as well as the id — this runs twice per ticket, once per
        source, so a message carrying only the id would emit two byte-identical
        warnings and leave an operator unable to tell one refused source from two.
        The ``run-artifacts:`` prefix names THIS module; the ``runs:`` prefix belongs
        to :mod:`~factory_console.file_adapter.runs`, and reusing it here would
        attribute a refusal to the reader that never ran.

        The reported path goes through :func:`~factory_console.file_adapter.runs.refusal_path`
        rather than being re-joined here, so a refused record's ``path`` is
        normalized exactly like every other :class:`ArtifactRead`'s — see that
        function for why both halves of that are module-wide invariants.
        """
        try:
            return reader(project_root, ticket_id)
        except PathTraversal:
            _LOGGER.warning(
                "run-artifacts: %r is not a path-safe segment; its %s artifact is not read",
                ticket_id,
                relative_dir,
            )
            return ArtifactRead(
                path=refusal_path(project_root, relative_dir, ticket_id), reason="unreadable"
            )


class FakeRunArtifactReader:
    """In-memory :class:`RunArtifactReader` for deterministic tests.

    Satisfies the Protocol structurally (no inheritance);
    ``isinstance(fake, RunArtifactReader)`` holds because it is
    ``@runtime_checkable``. Seeded with ``{ticket_id: ArtifactRead}`` per source, it
    touches no filesystem — which is the point: it lets a service composed over this
    port be exercised against a populated artifact without a real tree, the case
    :class:`~factory_console.file_adapter.fake.FakeFileAdapter` alone cannot reach.

    An unseeded ticket answers ``absent``, which is the REASON the real reader gives
    for a file that is not there; a fake whose unseeded case answered anything else
    would let a caller pass a test the real reader fails.

    The path it answers with is the UNRESOLVED join, and that is the one shape in
    which this fake deliberately differs from
    :class:`RealRunArtifactReader` — which reports resolved paths throughout, per
    :func:`~factory_console.file_adapter.runs.refusal_path`. Resolving here would
    mean a filesystem lookup, and performing no I/O is this class's whole reason to
    exist; the difference is also unobservable in the use it is built for, since
    fake-backed tests seed a synthetic ``rootPath`` (``/proj``) that exists on no
    disk, and resolving a path with no existing components yields exactly this join.
    Do not "fix" this by calling :meth:`~pathlib.Path.resolve` — assert on
    ``reason``/``data`` here, and let ``RealRunArtifactReader``'s own tests pin path
    normalization.
    """

    def __init__(
        self,
        results: dict[str, ArtifactRead] | None = None,
        receipts: dict[str, ArtifactRead] | None = None,
    ) -> None:
        """Seed per-source artifact reads; either map defaults to empty."""
        self._results = {} if results is None else results
        self._receipts = {} if receipts is None else receipts

    def read_result(self, project: Project, ticket_id: str) -> ArtifactRead:
        """Return the seeded result read, or ``absent`` when none was seeded."""
        return self._seeded_or_absent(
            self._results, project.rootPath, RESULTS_RELATIVE_DIR, ticket_id
        )

    def read_receipt(self, project: Project, ticket_id: str) -> ArtifactRead:
        """Return the seeded receipt read, or ``absent`` when none was seeded."""
        return self._seeded_or_absent(
            self._receipts, project.rootPath, RECEIPTS_RELATIVE_DIR, ticket_id
        )

    @staticmethod
    def _seeded_or_absent(
        seeded: dict[str, ArtifactRead],
        project_root: Path,
        relative_dir: Path,
        ticket_id: str,
    ) -> ArtifactRead:
        """The seeded read for ``ticket_id``, else ``absent`` at its unresolved path."""
        found = seeded.get(ticket_id)
        if found is not None:
            return found
        return ArtifactRead(path=project_root / relative_dir / f"{ticket_id}.json", reason="absent")
