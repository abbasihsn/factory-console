"""Unit tests for the registry/condition join.

The load-bearing property here is LENGTH PRESERVATION: ``len(result) ==
len(projects)``, always, with a degraded row transformed and never filtered. That is
the corollary of ``ARCHITECTURE.md``'s "The resolution invariant" (what could not be
established is *recorded, never dropped*) that this fold is responsible for, and
:func:`test_a_degraded_row_is_never_dropped` is its regression test — a fold that
"cleaned up" a ``path_missing`` row would tell the user they never registered a
project they did register.

Every test drives the fold through :class:`FakeProjectConditionProbe` rather than the
real one, because the rows below name synthetic paths (``/factory/alpha``) that exist
on no disk: the real probe would answer ``path_missing`` for all five and the mixed
case could not be written at all. That is the reason the port exists.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from factory_console.domain.registry import (
    RegisteredProject,
    RegistryEntry,
    RegistryEntryCondition,
)
from factory_console.file_adapter.project_condition import (
    FakeProjectConditionProbe,
    ProjectConditionProbe,
)
from factory_console.store.entries import resolve_entries

ADDED_AT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_project(slug: str) -> RegisteredProject:
    """A valid registry row named ``slug``: 32-hex id, absolute path, tz-aware ``addedAt``.

    The id is the slug's own bytes in hex, right-padded to the 32 lowercase hex digits
    :data:`~factory_console.domain.registry.REGISTERED_PROJECT_ID_PATTERN` demands,
    rather than a ``uuid4``: it is deterministic and distinct per slug, so a failure
    message names the same id every run and points back at the row it came from.
    """
    return RegisteredProject(
        id=slug.encode().hex()[:32].ljust(32, "0"),
        name=f"Project {slug}",
        path=Path("/factory") / slug,
        addedAt=ADDED_AT,
    )


class CountingProbe:
    """A :class:`ProjectConditionProbe` that records every path it was asked about.

    Written here rather than reaching into :class:`FakeProjectConditionProbe`'s
    internals, which expose no call count: the "exactly once per row" claim is about
    the FOLD's behaviour, so the counter belongs to the test that makes the claim and
    not to a fake the rest of the suite shares.
    """

    def __init__(self, inner: ProjectConditionProbe) -> None:
        self.inner = inner
        self.calls: list[Path] = []

    def probe(self, path: Path) -> RegistryEntryCondition:
        self.calls.append(path)
        return self.inner.probe(path)


# --------------------------------------------------------------------------- #
# Shape: length and order
# --------------------------------------------------------------------------- #


def test_an_empty_registry_yields_an_empty_list() -> None:
    assert resolve_entries([], FakeProjectConditionProbe()) == []


def test_every_row_becomes_exactly_one_entry() -> None:
    projects = [make_project(slug) for slug in ("alpha", "beta", "gamma")]
    entries = resolve_entries(projects, FakeProjectConditionProbe())

    assert len(entries) == len(projects)
    assert all(isinstance(entry, RegistryEntry) for entry in entries)


def test_input_order_is_preserved() -> None:
    """The listing's order is the store's order — the fold must not sort or regroup.

    Reordering by condition would be the other tempting "improvement", and it would
    make a project switcher's rows move under the user between reads for reasons that
    have nothing to do with what they did.
    """
    slugs = ("zulu", "alpha", "mike", "bravo")
    projects = [make_project(slug) for slug in slugs]

    entries = resolve_entries(projects, FakeProjectConditionProbe())

    assert [entry.project.path.name for entry in entries] == list(slugs)
    assert [entry.project for entry in entries] == projects


def test_a_one_shot_iterable_is_consumed_once_and_fully() -> None:
    """The store may hand over a cursor-backed generator, not a list."""
    projects = [make_project(slug) for slug in ("alpha", "beta")]
    stream: Iterable[RegisteredProject] = iter(projects)

    entries = resolve_entries(stream, FakeProjectConditionProbe())

    assert [entry.project for entry in entries] == projects


# --------------------------------------------------------------------------- #
# The join itself
# --------------------------------------------------------------------------- #


def test_five_rows_carry_the_five_distinct_conditions() -> None:
    """One row per union member: the fold reports what the probe said, row by row."""
    conditions: dict[str, RegistryEntryCondition] = {
        "alpha": "unreadable",
        "beta": "path_missing",
        "gamma": "not_a_project",
        "delta": "no_factory_dir",
        "epsilon": "ok",
    }
    projects = [make_project(slug) for slug in conditions]
    probe = FakeProjectConditionProbe(
        {project.path: conditions[project.path.name] for project in projects}
    )

    entries = resolve_entries(projects, probe)

    assert len(entries) == 5
    assert [entry.condition for entry in entries] == list(conditions.values())
    assert len({entry.condition for entry in entries}) == 5


def test_the_probe_is_called_exactly_once_per_row_with_the_registered_path() -> None:
    """One probe call per row, on the path as stored — never re-resolved, never re-probed.

    A second call per row would double the syscalls of every listing; a call on a
    re-resolved path would answer about a different project than the row names, which
    :class:`RegisteredProject`'s own docstring forbids.
    """
    projects = [make_project(slug) for slug in ("alpha", "beta", "gamma")]
    probe = CountingProbe(FakeProjectConditionProbe())

    resolve_entries(projects, probe)

    assert probe.calls == [project.path for project in projects]


def test_no_probe_call_is_made_for_an_empty_registry() -> None:
    probe = CountingProbe(FakeProjectConditionProbe())

    assert resolve_entries([], probe) == []
    assert probe.calls == []


# --------------------------------------------------------------------------- #
# THE INVARIANT: recorded, never dropped
# --------------------------------------------------------------------------- #


def test_a_degraded_row_is_never_dropped() -> None:
    """The regression test for ``len(result) == len(projects)``.

    A ``path_missing`` row sits BETWEEN two healthy ones, so a fold that filtered
    degraded rows fails on all three counts this asserts: the length, the surviving
    row's position, and its condition. Dropping it would render as "you never
    registered that project" — a false claim about the user's own action, and the
    "looks smaller and cleaner, reads as *more* information" failure
    ``ARCHITECTURE.md``'s resolution invariant names.
    """
    projects = [make_project(slug) for slug in ("healthy-one", "gone", "healthy-two")]
    probe = FakeProjectConditionProbe({projects[1].path: "path_missing"})

    entries = resolve_entries(projects, probe)

    assert len(entries) == 3
    assert entries[1].project == projects[1]
    assert entries[1].condition == "path_missing"
    assert [entry.condition for entry in entries] == ["ok", "path_missing", "ok"]


def test_a_registry_where_every_row_is_degraded_still_yields_every_row() -> None:
    """The degenerate case of the same rule: nothing healthy is left to make the list look fine."""
    projects = [make_project(slug) for slug in ("alpha", "beta", "gamma")]
    probe = FakeProjectConditionProbe(default="unreadable")

    entries = resolve_entries(projects, probe)

    assert [entry.project for entry in entries] == projects
    assert {entry.condition for entry in entries} == {"unreadable"}


def test_the_counting_probe_satisfies_the_protocol_structurally() -> None:
    assert isinstance(CountingProbe(FakeProjectConditionProbe()), ProjectConditionProbe)
