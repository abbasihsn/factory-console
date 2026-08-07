"""In-memory :class:`FakeProjectRegistry` — a side-effect-free ProjectRegistry for tests.

The registry-side counterpart of
:class:`~factory_console.file_adapter.fake.FakeFileAdapter`: it holds the rows in
plain dicts and answers every
:class:`~factory_console.store.registry_protocol.ProjectRegistry` method out of
them, so a backend test can exercise the v3.0 registry endpoints without a
database, a temp directory, or a single filesystem write. It satisfies the port
STRUCTURALLY (no inheritance), exactly as the two file-adapter fakes do —
``isinstance(fake, ProjectRegistry)`` holds because the Protocol is
``@runtime_checkable``.

**No filesystem access, and no mutation of caller-supplied values.** The seeded
``projects`` list is copied at construction, so a caller mutating their list
afterwards cannot reach in here, and :meth:`FakeProjectRegistry.list_projects`
hands back a fresh list every call. The rows themselves are frozen
:class:`~factory_console.domain.registry.RegisteredProject` models, so sharing
them is already safe. The one thing this module does read is the *shape* of a
path: :func:`~factory_console.store.paths.canonical_project_path` calls
``resolve(strict=False)``, which normalises symlinks it can see — that rule is
REUSED rather than re-implemented, because a fake that decided for itself what
"the same project" means is precisely the duplicate-detection drift
``tests/_registry_contract.py`` exists to prevent.

Two seams are injectable — ``id_factory`` and ``clock`` — so a test can assert on
EXACT ids and EXACT ``addedAt`` values without freezing global time or patching
:mod:`uuid`. Both default to what the port documents
(``uuid4().hex``/``datetime.now(UTC)``), so a test that does not care about
either simply does not pass them.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from factory_console.domain.registry import RegisteredProject
from factory_console.store.paths import canonical_project_path, default_project_name
from factory_console.store.registry_protocol import DuplicateProjectPath, ProjectNotRegistered


class FakeProjectRegistry:
    """In-memory :class:`ProjectRegistry` implementation for deterministic tests.

    Holds three pieces of state: the rows keyed by id (insertion-ordered, as
    every :class:`dict` is), a canonical-path index that answers
    :meth:`find_by_path` and decides duplicates, and the selected id. All three
    are private; nothing here is shared with the caller by reference.
    """

    def __init__(
        self,
        projects: list[RegisteredProject] | None = None,
        *,
        selected_id: str | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Seed the registry with pre-built rows and an optional selection.

        ``projects`` are the rows to start with — copied, never referenced, so a
        caller's later ``append`` does not register a project behind this
        object's back. Each seeded row is re-stored with its ``path`` replaced by
        the CANONICAL form and indexed under that same form, so a row seeded
        through a non-canonical spelling cannot leave the fake in a shape its
        SQLite counterpart could never write, and a lookup for any spelling of a
        seeded path finds it exactly as it would for one added through
        :meth:`add_project`. Seeding two rows that collide on a canonical
        path raises :class:`DuplicateProjectPath`, and seeding two rows with one
        id raises :class:`ValueError` — the fake refuses to start in a state its
        SQLite counterpart's ``UNIQUE`` index and primary key would have refused
        to reach.

        ``selected_id`` goes through :meth:`set_selected_project`, so an id that
        names no seeded row raises :class:`ProjectNotRegistered` here for the same
        reason it does there: a registry that starts out pointing at nothing is
        the failure the no-fallback rule exists to make visible.

        ``id_factory`` and ``clock`` are the determinism seams. ``id_factory``
        mints the id for each :meth:`add_project` (default ``uuid4().hex``) and
        ``clock`` stamps its ``addedAt`` (default ``datetime.now(UTC)``), so a
        test can hand in ``itertools.count``-style scripted values and assert the
        exact row it expects — instead of re-deriving the id from the return
        value it is supposed to be checking, or comparing timestamps with a
        tolerance. A ``clock`` returning a naive datetime is rejected by
        :class:`~factory_console.domain.registry.RegisteredProject` itself, which
        is where that rule belongs.
        """
        self._id_factory = id_factory if id_factory is not None else lambda: uuid4().hex
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._rows: dict[str, RegisteredProject] = {}
        self._id_by_path: dict[Path, str] = {}
        self._path_key_by_id: dict[str, Path] = {}
        self._selected_id: str | None = None
        # Each seeded row is stored and indexed under the CANONICAL form of its own
        # path, so a seeded row can never carry a ``path`` the SQLite store would
        # have refused to write, and it is found by the same spellings an added one
        # would be.
        for project in projects or ():
            canonical = canonical_project_path(project.path)
            self._insert(project.model_copy(update={"path": canonical}), canonical)
        self.set_selected_project(selected_id)

    def add_project(self, path: Path | str, name: str | None = None) -> RegisteredProject:
        """Register ``path`` and return the stored row. See the port for the contract.

        The duplicate check runs BEFORE the id is minted, so a rejected add does
        not silently consume a value from a scripted ``id_factory`` and desync
        every later assertion in the test that supplied it.
        """
        canonical = canonical_project_path(path)
        self._reject_duplicate_path(canonical)
        row = RegisteredProject(
            id=self._id_factory(),
            name=default_project_name(canonical) if name is None else name,
            path=canonical,
            addedAt=self._clock(),
        )
        self._insert(row, canonical)
        return row

    def list_projects(self) -> list[RegisteredProject]:
        """Return every row ordered by ``(addedAt, id)``, as a NEW list each call."""
        return sorted(self._rows.values(), key=lambda row: (row.addedAt, row.id))

    def get_project(self, project_id: str) -> RegisteredProject | None:
        """Return the row with ``project_id``, or ``None`` when there is none."""
        return self._rows.get(project_id)

    def find_by_path(self, path: Path | str) -> RegisteredProject | None:
        """Return the row registered at ``path``, or ``None``.

        Canonicalises first, so :class:`~factory_console.store.paths.InvalidProjectPath`
        propagates for input that has no canonical form to compare against.
        """
        found_id = self._id_by_path.get(canonical_project_path(path))
        return None if found_id is None else self._rows[found_id]

    def remove_project(self, project_id: str) -> bool:
        """Delete the row with ``project_id``. ``True`` if one was removed.

        Clears the selection when the removed row WAS the selected one, mirroring
        the schema's ``ON DELETE SET NULL`` rather than leaving
        :meth:`get_selected_project` pointing at a row that is gone.
        """
        if self._rows.pop(project_id, None) is None:
            return False
        del self._id_by_path[self._path_key_by_id.pop(project_id)]
        if self._selected_id == project_id:
            self._selected_id = None
        return True

    def get_selected_project(self) -> RegisteredProject | None:
        """Return the selected row, or ``None`` — never a fallback to another row."""
        if self._selected_id is None:
            return None
        return self._rows[self._selected_id]

    def set_selected_project(self, project_id: str | None) -> RegisteredProject | None:
        """Select ``project_id``, or clear the selection with ``None``.

        Raises:
            ProjectNotRegistered: ``project_id`` names no row.
        """
        if project_id is None:
            self._selected_id = None
            return None
        row = self._rows.get(project_id)
        if row is None:
            raise ProjectNotRegistered(project_id)
        self._selected_id = project_id
        return row

    def _insert(self, row: RegisteredProject, path_key: Path) -> None:
        """Store ``row`` under ``path_key``, refusing a collision on either key.

        The single writer of all three state dicts, so the path index and the
        per-id index key cannot come to disagree about which key to delete on
        :meth:`remove_project`. The key is passed in rather than re-derived from
        ``row.path``: ``RegisteredProject.path`` documents that a consumer must
        NOT re-resolve it, and a re-resolve here would also be free to answer
        differently from the resolve that produced the index entry.
        """
        self._reject_duplicate_path(path_key)
        if row.id in self._rows:
            raise ValueError(f"id {row.id} is already registered")
        self._rows[row.id] = row
        self._id_by_path[path_key] = row.id
        self._path_key_by_id[row.id] = path_key

    def _reject_duplicate_path(self, path_key: Path) -> None:
        """Raise :class:`DuplicateProjectPath` when ``path_key`` is already registered."""
        existing_id = self._id_by_path.get(path_key)
        if existing_id is not None:
            raise DuplicateProjectPath(path_key, existing_id)
