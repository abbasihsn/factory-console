"""Unit tests for the read-only project-condition probe.

The load-bearing property here is TOTALITY: every case below drives the classifier
to a NAMED :data:`~factory_console.domain.registry.RegistryEntryCondition` rather
than to an exception, because a probe that raised on one bad row would fail a whole
registry listing and delete every healthy project from the user's screen. Every test
in this file is therefore also a totality test — a raise fails it as an error before
any assertion runs — and
:func:`test_every_condition_is_reachable_and_nothing_raises` states that claim
explicitly across all five members of the union.

The healthy cases run against the COMMITTED fixtures rather than a tree built here:
``tests/fixtures/projects/factory_layout`` (manifest plus ``.factory/``) must read
``ok`` and ``tests/fixtures/projects/minimal`` (manifest, no ``.factory/``) must read
``no_factory_dir``. Those fixtures are what the rest of the suite already treats as a
real project, so a classifier that disagreed with them would be disagreeing with the
console's own idea of what it can open — not merely with a tree this file invented.

The degraded cases need a tree that does not exist yet, or one with a mode no
repository can carry, so they build under ``tmp_path``. The ``0o000`` case is skipped
under root, which bypasses permission bits entirely and would read the directory
happily.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import get_args

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

from factory_console.domain.registry import RegistryEntryCondition
from factory_console.file_adapter import project_condition as project_condition_module
from factory_console.file_adapter.discovery import MANIFEST_RELPATH
from factory_console.file_adapter.project_condition import (
    FACTORY_RELATIVE_DIR,
    FakeProjectConditionProbe,
    ProjectConditionProbe,
    RealProjectConditionProbe,
    classify_project_path,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "projects"
FACTORY_LAYOUT = FIXTURES / "factory_layout"
MINIMAL = FIXTURES / "minimal"


def make_project(root: Path, *, with_factory_dir: bool) -> Path:
    """Build a project tree at ``root``: always the manifest, optionally ``.factory/``."""
    manifest = root / MANIFEST_RELPATH
    manifest.parent.mkdir(parents=True)
    manifest.write_text("[]")
    if with_factory_dir:
        (root / FACTORY_RELATIVE_DIR).mkdir()
    return root


@pytest.fixture
def unreadable_dir(tmp_path: Path) -> Iterator[Path]:
    """A directory the process may not search, restored to ``0o755`` on teardown.

    The restore is not tidiness: pytest's own ``tmp_path`` cleanup walks the tree it
    created, and a ``0o000`` directory left behind fails that walk — turning a passing
    test into a teardown error in an unrelated place. It runs through the fixture's
    own finalization so it happens even when the assertion below fails.
    """
    blocked = tmp_path / "blocked"
    make_project(blocked, with_factory_dir=True)
    blocked.chmod(0o000)
    try:
        yield blocked
    finally:
        blocked.chmod(0o755)


# --------------------------------------------------------------------------- #
# The classifier, against the committed fixtures
# --------------------------------------------------------------------------- #


def test_a_project_with_manifest_and_factory_dir_is_ok() -> None:
    assert classify_project_path(FACTORY_LAYOUT) == "ok"


def test_a_project_without_a_factory_dir_is_no_factory_dir() -> None:
    assert classify_project_path(MINIMAL) == "no_factory_dir"


# --------------------------------------------------------------------------- #
# The classifier, against trees no repository can carry
# --------------------------------------------------------------------------- #


def test_a_path_that_was_never_created_is_path_missing(tmp_path: Path) -> None:
    assert classify_project_path(tmp_path / "never-created") == "path_missing"


def test_a_path_under_a_regular_file_is_path_missing(tmp_path: Path) -> None:
    """A path whose PARENT component is a file names nothing — ``ENOTDIR``, not a project.

    This is the case the classifier catches :class:`NotADirectoryError` for, and it
    reads ``path_missing`` for the same reason a deleted path does: nothing exists at
    the registered path any more.
    """
    parent = tmp_path / "file.txt"
    parent.write_text("not a directory")
    assert classify_project_path(parent / "project") == "path_missing"


def test_a_regular_file_is_not_a_project(tmp_path: Path) -> None:
    regular_file = tmp_path / "project.txt"
    regular_file.write_text("i am a file")
    assert classify_project_path(regular_file) == "not_a_project"


def test_an_empty_directory_is_not_a_project(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert classify_project_path(empty) == "not_a_project"


def test_a_directory_without_the_manifest_is_not_a_project(tmp_path: Path) -> None:
    """Planning directories but no ``docs/planning/tickets.json`` — the console looked."""
    planning = tmp_path / "no-manifest" / MANIFEST_RELPATH.parent
    planning.mkdir(parents=True)
    (planning / "ROADMAP.md").write_text("# Roadmap")
    assert classify_project_path(tmp_path / "no-manifest") == "not_a_project"


def test_a_directory_with_the_manifest_but_no_factory_dir_is_no_factory_dir(
    tmp_path: Path,
) -> None:
    """The ordinary state of a fresh clone: ``.factory/`` is gitignored."""
    project = make_project(tmp_path / "fresh-clone", with_factory_dir=False)
    assert classify_project_path(project) == "no_factory_dir"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_an_unsearchable_directory_is_unreadable_not_not_a_project(unreadable_dir: Path) -> None:
    """A permission error is NEVER answered as the more permissive ``not_a_project``.

    The tree behind this mode is a COMPLETE project — manifest and ``.factory/`` both
    present — so ``not_a_project`` here would not merely be less precise, it would be
    false: "I could not look" is not "I looked and it is not a project", and reporting
    the second sends an operator hunting for a project that was there all along.
    """
    assert classify_project_path(unreadable_dir) == "unreadable"


def test_a_symlink_loop_is_unreadable(tmp_path: Path) -> None:
    """``ELOOP`` on the path ITSELF — an ``OSError`` that is not an absence.

    The other branch of the first ``stat``, and the one no ``chmod`` can reach: the
    path is neither missing nor readable, so the console has established nothing about
    it and must say so.
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second)
    second.symlink_to(first)
    assert classify_project_path(first) == "unreadable"


def test_a_factory_dir_that_cannot_be_examined_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last probe degrades too: a readable manifest does not make ``ok`` safe to claim.

    Monkeypatched rather than chmod'd because no mode reaches this state on its own —
    a directory unsearchable enough to fail the ``.factory/`` probe fails the manifest
    probe first, one step earlier. The failure it stands in for is real regardless (an
    I/O error on the ``.factory/`` inode, a stale network mount), and the answer must
    be ``unreadable`` and never ``no_factory_dir``: reporting a degraded-but-usable
    project would tell the UI that run-state is legitimately absent when the console
    simply could not look.
    """
    project = make_project(tmp_path / "opaque-factory-dir", with_factory_dir=True)
    original_stat = Path.stat

    def raise_on_factory_dir(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self.name == FACTORY_RELATIVE_DIR.name:
            raise PermissionError(f"cannot examine {self}")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", raise_on_factory_dir)
    assert classify_project_path(project) == "unreadable"


# --------------------------------------------------------------------------- #
# TOTALITY and coverage of the union
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_every_condition_is_reachable_and_nothing_raises(
    tmp_path: Path, unreadable_dir: Path
) -> None:
    """Drive one input per union member through the port, and let nothing escape.

    The port promises to be TOTAL, so the absence of a raise IS the assertion — every
    call below would fail this test as an error, not as a mismatch, if the probe let
    an exception out. The second assertion pins the other half: the observed answers
    are exactly ``get_args(RegistryEntryCondition)``, so a member the classifier can
    never produce (or one added to the union without a probe path) fails here rather
    than shipping as a condition the console defines and never reports.
    """
    regular_file = tmp_path / "project.txt"
    regular_file.write_text("i am a file")
    probe = RealProjectConditionProbe()

    observed = {
        probe.probe(unreadable_dir),
        probe.probe(tmp_path / "never-created"),
        probe.probe(regular_file),
        probe.probe(MINIMAL),
        probe.probe(FACTORY_LAYOUT),
    }

    assert observed == set(get_args(RegistryEntryCondition))


def test_the_real_probe_satisfies_the_protocol_structurally() -> None:
    assert isinstance(RealProjectConditionProbe(), ProjectConditionProbe)


def test_the_real_probe_delegates_to_the_classifier(tmp_path: Path) -> None:
    project = make_project(tmp_path / "delegated", with_factory_dir=True)
    assert RealProjectConditionProbe().probe(project) == classify_project_path(project)


# --------------------------------------------------------------------------- #
# The fake: seeded answers, no filesystem
# --------------------------------------------------------------------------- #


def test_the_fake_satisfies_the_protocol_structurally() -> None:
    assert isinstance(FakeProjectConditionProbe(), ProjectConditionProbe)


def test_the_fake_answers_a_seeded_path_with_its_seeded_condition() -> None:
    fake = FakeProjectConditionProbe({Path("/factory/demo-project"): "path_missing"})
    assert fake.probe(Path("/factory/demo-project")) == "path_missing"


def test_the_fake_answers_an_unseeded_path_with_the_default() -> None:
    fake = FakeProjectConditionProbe({Path("/factory/demo-project"): "path_missing"})
    assert fake.probe(Path("/factory/other")) == "ok"


def test_the_fake_default_is_configurable() -> None:
    fake = FakeProjectConditionProbe(default="unreadable")
    assert fake.probe(Path("/factory/anything")) == "unreadable"


@pytest.mark.parametrize(
    "spelling",
    ["/factory/demo-project/", "/factory/./demo-project", "/factory//demo-project"],
)
def test_the_fake_normalizes_the_spelling_of_a_seeded_path(spelling: str) -> None:
    """A test's spelling of a path must not decide whether its seed is found."""
    fake = FakeProjectConditionProbe({Path("/factory/demo-project"): "no_factory_dir"})
    assert fake.probe(Path(spelling)) == "no_factory_dir"


def test_the_fake_normalizes_the_spelling_of_its_seed_keys() -> None:
    """Normalization applies to the SEED as well as to the probed path."""
    fake = FakeProjectConditionProbe({Path("/factory/./demo-project/"): "not_a_project"})
    assert fake.probe(Path("/factory/demo-project")) == "not_a_project"


def test_the_fake_answers_a_path_that_exists_on_no_disk(tmp_path: Path) -> None:
    """The reason this port exists: a seeded ``ok`` for a path the real probe calls missing.

    ``tmp_path / "gone"`` is deliberately never created, so
    :func:`classify_project_path` answers ``path_missing`` for it while the fake
    answers what the test seeded — proving the fake reads its map and not the disk.
    """
    absent = tmp_path / "gone"
    fake = FakeProjectConditionProbe({absent: "ok"})
    assert fake.probe(absent) == "ok"
    assert classify_project_path(absent) == "path_missing"


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# --------------------------------------------------------------------------- #


def test_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(project_condition_module)


def test_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(project_condition_module)
