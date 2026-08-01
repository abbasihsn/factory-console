"""Unit tests for the read-only factory run-state prober.

Exercises :func:`find_run_state_dir` (fallback probe order) and
:func:`probe_ticket_state` (marker precedence, the ``unknown``/``absent``
defaults, and the defense-in-depth path-traversal guard), building run-state
trees on the fly under ``tmp_path``. A final GUARD test parses this module's
target source and asserts the read-only invariant: it contains no
filesystem-mutating call.
"""

import os
import shutil
from pathlib import Path

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

from factory_console.domain import RunState
from factory_console.domain.run_state_source import RunStateSource
from factory_console.file_adapter import run_state as run_state_module
from factory_console.file_adapter import write_gate
from factory_console.file_adapter.run_state import (
    PathTraversal,
    find_run_state_dir,
    is_run_state_marker,
    probe_ticket_state,
    probe_ticket_state_from_source,
    run_state_resolver,
)

# Each on-disk state directory name paired with the enum member it must map to
# (``in-flight`` -> RunState.in_flight — mapped by value, not string guessing).
_STATE_TO_ENUM = [
    ("todo", RunState.todo),
    ("in-flight", RunState.in_flight),
    ("ready", RunState.ready),
    ("merged", RunState.merged),
]


def _place_marker(run_state_dir: Path, state: str, ticket_id: str, *, as_dir: bool) -> Path:
    """Create ``<run_state_dir>/<state>/<ticket_id>`` as a file or a directory."""
    state_dir = run_state_dir / state
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / ticket_id
    if as_dir:
        marker.mkdir()
    else:
        marker.write_text("")
    return marker


# --------------------------------------------------------------------------- #
# probe_ticket_state — unknown / absent defaults
# --------------------------------------------------------------------------- #


def test_no_run_state_dir_resolves_to_unknown() -> None:
    # Absence of the run-state directory -> RunState.unknown (per ARCHITECTURE).
    assert probe_ticket_state(None, "CAD-118") == RunState.unknown, (
        "a missing run-state dir must resolve to RunState.unknown"
    )


def test_present_dir_listing_another_ticket_resolves_to_absent(tmp_path: Path) -> None:
    # The ORIGINAL T80 rule, unchanged by the vacuous amendment: the directory lists
    # CAD-100, so it IS exercising authority, and it does not list CAD-118.
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    _place_marker(run_state_dir, "merged", "CAD-100", as_dir=False)
    assert probe_ticket_state(run_state_dir, "CAD-118") == RunState.absent, (
        "a run-state dir that lists another ticket but no marker for this id must "
        "resolve RunState.absent (the directory resolved and does not list this ticket)"
    )


def test_a_vacuous_dir_resolves_to_unknown_for_every_id(tmp_path: Path) -> None:
    # T80's amendment, gap 1: a source that names NOBODY says nothing about anybody.
    # An empty-but-valid run-state dir must not answer `absent` for every ticket —
    # that would refuse every write in the project (a read-only lockout) on a plan
    # the factory has simply never run on.
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    for ticket_id in ("CAD-118", "CAD-999", "T01"):
        assert probe_ticket_state(run_state_dir, ticket_id) is RunState.unknown

    # Same when the state subdirectories exist but hold no marker — the shape the
    # factory leaves behind before it has seeded anything.
    for state in ("merged", "ready", "in-flight", "todo"):
        (run_state_dir / state).mkdir()
    assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.unknown


def test_one_marker_anywhere_makes_the_dir_non_vacuous(tmp_path: Path) -> None:
    # The boundary between the two tests above, walked one state at a time: a single
    # marker under ANY state subdir is enough for the directory to start answering
    # `absent` for the ids it does not name.
    for state in ("merged", "ready", "in-flight", "todo"):
        run_state_dir = tmp_path / state / "run-state"
        run_state_dir.mkdir(parents=True)
        assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.unknown
        _place_marker(run_state_dir, state, "CAD-100", as_dir=False)
        assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.absent


def test_the_resolver_agrees_with_the_probe_on_a_vacuous_directory(tmp_path: Path) -> None:
    # The batch path settles "does this source list anybody?" ONCE, so it must reach
    # the same answer as the single-ticket prober above — otherwise a list projection
    # and a write gate would disagree about the same directory.
    run_state_dir = tmp_path / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    source = RunStateSource(kind="directory", path=run_state_dir)

    resolve = run_state_resolver(source)
    assert [resolve(f"CAD-{n}") for n in range(5)] == [RunState.unknown] * 5

    # One marker later the directory lists somebody, and a fresh resolver refuses the
    # ids it does not name — the amendment must not have removed the `absent` answer.
    _place_marker(run_state_dir, "todo", "CAD-100", as_dir=False)
    resolve_again = run_state_resolver(source)
    assert resolve_again("CAD-100") is RunState.todo
    assert resolve_again("CAD-118") is RunState.absent


def test_a_vanished_dir_resolves_to_unknown_not_absent(tmp_path: Path) -> None:
    # The directory-form counterpart of the JSON form's ``readable=False`` rule: a
    # path discovered by load_project but gone (or replaced by a non-directory) by
    # the time it is probed cannot be trusted to mean "lists nothing". Answering
    # ``absent`` here would flip an entire project read-only — every ticket refused
    # 409 — on a transient disappearance, where ``unknown`` keeps it editable.
    assert probe_ticket_state(tmp_path / "gone", "CAD-118") is RunState.unknown

    not_a_dir = tmp_path / "run-state-file"
    not_a_dir.write_text("not a directory", encoding="utf-8")
    assert probe_ticket_state(not_a_dir, "CAD-118") is RunState.unknown


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_an_unreadable_dir_resolves_to_unknown_not_absent(tmp_path: Path) -> None:
    # The same "cannot be trusted -> unknown" rule for a directory that EXISTS but
    # cannot be stat'ed (the factory created it mode-0700 under a different uid).
    # ``Path.exists()`` only swallows ENOENT/ENOTDIR/EBADF/ELOOP, so on EACCES it
    # RAISES — without the OSError guard in probe_ticket_state this escapes the
    # read-only prober and 500s every list/read/write request for the project.
    run_state_dir = tmp_path / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    run_state_dir.chmod(0o000)
    try:
        assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.unknown
    finally:
        run_state_dir.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_an_unreadable_dir_is_reported_once_per_resolver_not_once_per_ticket(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Log-volume symmetry with the JSON form, which parses once and so reports an
    # unreadable file once. The directory form probes per ticket, so the SOURCE-level
    # readability question is settled once in run_state_resolver: a 200-ticket list
    # projection against an unstattable run-state dir must not emit 200 identical
    # warnings. Every ticket must still answer the mutable `unknown`.
    run_state_dir = tmp_path / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    run_state_dir.chmod(0o000)
    try:
        with caplog.at_level("WARNING", logger=run_state_module._LOGGER.name):
            resolve = run_state_resolver(RunStateSource(kind="directory", path=run_state_dir))
            states = [resolve(f"CAD-{n}") for n in range(20)]
    finally:
        run_state_dir.chmod(0o755)

    assert states == [RunState.unknown] * 20
    assert len([r for r in caplog.records if "run-state" in r.getMessage()]) == 1


def test_scaffolding_files_do_not_make_a_vacuous_dir_non_vacuous(tmp_path: Path) -> None:
    # A `.gitkeep` is how an otherwise-empty state subdirectory gets committed to git;
    # `.DS_Store` and editor swap files arrive the same way. None of them names a
    # TICKET, so none of them may flip the source from vacuous to authoritative — if
    # one did, every ticket in the project would resolve `absent` and every write
    # would 409, which is precisely the project-wide read-only lockout the vacuous
    # rule exists to prevent. Asserted as the resolved STATE, not as a helper's
    # return value.
    run_state_dir = tmp_path / "run-state"
    for state in ("merged", "ready", "in-flight", "todo"):
        (run_state_dir / state).mkdir(parents=True)
    (run_state_dir / "todo" / ".gitkeep").write_text("", encoding="utf-8")
    (run_state_dir / "merged" / ".DS_Store").write_text("", encoding="utf-8")

    assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.unknown

    # And the batch form must agree, or a list projection would paint every ticket
    # read-only while the single-ticket gate let the write through.
    resolve = run_state_resolver(RunStateSource(kind="directory", path=run_state_dir))
    assert resolve("CAD-118") is RunState.unknown

    # One REAL marker still makes it authoritative — the guard against over-correcting
    # into "a directory is never non-vacuous".
    _place_marker(run_state_dir, "todo", "CAD-1", as_dir=False)
    assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.absent


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_unenumerable_state_dirs_do_not_read_as_vacuous(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The gate-bypass guard. State subdirectories that are traversable but NOT
    # readable (mode 0711 — the factory running under a different uid) pass every
    # `exists()`/`is_dir()` check, because those need only `+x` on the parent, while
    # `iterdir()` raises EACCES. Reading that as "lists nobody" made the resolver
    # short-circuit to a constant mutable `unknown` for EVERY ticket, silently
    # disabling the write gate on a project whose markers say `merged`.
    run_state_dir = tmp_path / "run-state"
    for state in ("merged", "ready", "in-flight", "todo"):
        (run_state_dir / state).mkdir(parents=True)
    _place_marker(run_state_dir, "merged", "CAD-1", as_dir=False)
    for state in ("merged", "ready", "in-flight", "todo"):
        (run_state_dir / state).chmod(0o111)
    try:
        with caplog.at_level("WARNING", logger=run_state_module._LOGGER.name):
            resolve = run_state_resolver(RunStateSource(kind="directory", path=run_state_dir))
            # The marker is still readable by `exists()`, so the ticket a lane owns
            # must keep its read-only state rather than fall into the mutable set.
            merged_state = resolve("CAD-1")
            # An id with no marker stays the mutable `unknown`: "I could not tell"
            # never hardens into the refusing `absent`.
            unmarked_state = resolve("CAD-118")
            probed = probe_ticket_state(run_state_dir, "CAD-1")
    finally:
        for state in ("merged", "ready", "in-flight", "todo"):
            (run_state_dir / state).chmod(0o755)

    assert merged_state is RunState.merged
    assert unmarked_state is RunState.unknown
    # The single-ticket prober must agree with the batch form, as everywhere else.
    assert probed is RunState.merged
    # A degradation that widens the write gate has to leave a trace — unlike the
    # ordinary vacuous case, which is deliberately silent.
    assert [r for r in caplog.records if "could not be enumerated" in r.getMessage()]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permission bits")
def test_a_non_searchable_state_dir_does_not_hide_a_readable_marker(tmp_path: Path) -> None:
    # The OTHER gate-bypass shape, and the one mode-0711 above cannot reach. A state
    # subdirectory that is readable but NOT searchable (mode 0600) makes `exists()`
    # raise EACCES rather than `iterdir()` — and `merged` is probed FIRST, so aborting
    # the precedence walk on the first error made every id raise before `ready`/
    # `in-flight`/`todo` were ever tried. Both callers map that to the MUTABLE
    # `unknown`, so ONE restricted directory silently disabled the write gate for the
    # whole project, including tickets whose markers sit in perfectly readable state
    # directories. Asserted as the resolved state AND as the gate consequence.
    run_state_dir = tmp_path / "run-state"
    for state in ("merged", "ready", "in-flight", "todo"):
        (run_state_dir / state).mkdir(parents=True)
    # A ticket a lane owns, in a directory that stays fully readable throughout.
    _place_marker(run_state_dir, "ready", "CAD-118", as_dir=False)
    _place_marker(run_state_dir, "merged", "CAD-1", as_dir=False)
    (run_state_dir / "merged").chmod(0o600)
    try:
        resolve = run_state_resolver(RunStateSource(kind="directory", path=run_state_dir))
        batch_state = resolve("CAD-118")
        probed = probe_ticket_state(run_state_dir, "CAD-118")
    finally:
        (run_state_dir / "merged").chmod(0o755)

    assert batch_state is RunState.ready
    # The single-ticket prober must agree with the batch form, as everywhere else.
    assert probed is RunState.ready
    # The point of the fix: a lane-owned ticket stays refused for BOTH writes. Before,
    # this resolved `unknown` and was editable and deletable.
    assert batch_state not in write_gate.MUTABLE_STATES
    assert batch_state not in write_gate.DELETABLE_STATES


def test_the_resolver_answers_unknown_not_absent_when_the_source_vanishes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # `lists_someone` is settled ONCE at resolver construction, but the answer it
    # licenses ("the source lists others, so it definitively does not list you") stops
    # being true when the source goes away. One resolver serves a whole list/deps/graph
    # request, so a factory rewriting run-state mid-request (rm+recreate, or an atomic
    # rename swap) leaves every remaining id with no marker — and answering `absent`
    # there would turn a transient disappearance into a project-wide read-only lockout.
    # `probe_ticket_state` already re-checked `is_dir()`; the batch form must too, or
    # the two forms answer differently for one filesystem.
    run_state_dir = tmp_path / "run-state"
    (run_state_dir / "todo").mkdir(parents=True)
    _place_marker(run_state_dir, "todo", "CAD-100", as_dir=False)
    source = RunStateSource(kind="directory", path=run_state_dir)

    resolve = run_state_resolver(source)
    # While the source is there it is authoritative, and an id it omits IS `absent`.
    assert resolve("CAD-118") is RunState.absent

    shutil.rmtree(run_state_dir)
    with caplog.at_level("WARNING", logger=run_state_module._LOGGER.name):
        after = [resolve(f"CAD-{n}") for n in range(20)]

    assert after == [RunState.unknown] * 20
    # Settled once, logged once: a 200-ticket projection must not emit 200 lines.
    # Counted BEFORE the single-ticket probe below, which emits its own equivalent
    # warning and would otherwise be miscounted as a second resolver line.
    resolver_warnings = [r for r in caplog.records if "no longer a directory" in r.getMessage()]
    assert len(resolver_warnings) == 1

    # And the single-ticket prober agrees, which is the guarantee that was broken.
    assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.unknown


def test_a_stale_resolver_cannot_widen_the_write_gate(tmp_path: Path) -> None:
    # Vacuity is settled ONCE per resolver — re-deriving it per ticket is the
    # O(tickets x states) re-scan the batch form exists to avoid — so a marker set that
    # changes under a LIVE resolver is not observed. The residual runs in both
    # directions, and this pins the one that matters: markers added to a directory that
    # was VACUOUS at construction leave the stale resolver answering the MUTABLE
    # `unknown` while the source has since become authoritative.
    #
    # That is tolerable only because it cannot reach the gate. `ensure_mutable` /
    # `ensure_deletable` resolve through `probe_ticket_state_from_source`, which builds
    # a FRESH resolver every call, so `lists_someone` is never stale at gate time. The
    # only long-lived resolver is the read-only list/deps/graph projection's, where a
    # stale badge lasts one request. This test is the guard on that reasoning: if
    # someone ever caches a resolver across requests, or routes the gate through one,
    # the second assertion breaks.
    run_state_dir = tmp_path / "run-state"
    for state in ("merged", "ready", "in-flight", "todo"):
        (run_state_dir / state).mkdir(parents=True)
    source = RunStateSource(kind="directory", path=run_state_dir)

    stale = run_state_resolver(source)  # built while the directory lists nobody
    assert stale("CAD-118") is RunState.unknown

    _place_marker(run_state_dir, "merged", "CAD-100", as_dir=False)

    # The stale resolver keeps its construction-time answer — the accepted residual.
    assert stale("CAD-118") is RunState.unknown
    # But every gate-facing entry point re-resolves, so the refusal is not missed.
    assert probe_ticket_state_from_source(source, "CAD-118") is RunState.absent
    assert probe_ticket_state(run_state_dir, "CAD-118") is RunState.absent
    assert probe_ticket_state_from_source(source, "CAD-118") not in write_gate.MUTABLE_STATES


# --------------------------------------------------------------------------- #
# probe_ticket_state — a marker as a FILE or a DIR maps to the right enum
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("as_dir", [False, True], ids=["file-marker", "dir-marker"])
@pytest.mark.parametrize(
    "state, expected",
    _STATE_TO_ENUM,
    ids=[state for state, _ in _STATE_TO_ENUM],
)
def test_marker_maps_to_enum(tmp_path: Path, state: str, expected: RunState, as_dir: bool) -> None:
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    _place_marker(run_state_dir, state, "CAD-118", as_dir=as_dir)
    kind = "dir" if as_dir else "file"
    assert probe_ticket_state(run_state_dir, "CAD-118") == expected, (
        f"a '{state}' marker present as a {kind} must resolve to {expected!r}"
    )


# --------------------------------------------------------------------------- #
# probe_ticket_state — marker precedence (highest state wins)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "present, expected",
    [
        (("merged", "ready"), RunState.merged),
        (("ready", "in-flight"), RunState.ready),
        (("in-flight", "todo"), RunState.in_flight),
    ],
    ids=["merged-beats-ready", "ready-beats-in-flight", "in-flight-beats-todo"],
)
def test_marker_precedence(tmp_path: Path, present: tuple[str, str], expected: RunState) -> None:
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    for state in present:
        _place_marker(run_state_dir, state, "CAD-118", as_dir=False)
    assert probe_ticket_state(run_state_dir, "CAD-118") == expected, (
        f"with markers {present} present, the highest-precedence state {expected!r} must win"
    )


# --------------------------------------------------------------------------- #
# probe_ticket_state — path-traversal ids are refused before any FS lookup
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    [
        pytest.param("..", id="dotdot"),
        pytest.param(".", id="dot"),
        pytest.param("foo/bar", id="slash-segment"),
        pytest.param("", id="empty"),
        pytest.param("a/../b", id="embedded-traversal"),
        pytest.param("CAD-118\n", id="trailing-newline"),
    ],
)
def test_traversal_ticket_id_is_refused(tmp_path: Path, bad_id: str) -> None:
    # Pass a real, existing run-state dir so a raised PathTraversal proves it is
    # the id validation firing, not a missing directory.
    run_state_dir = tmp_path / "run-state"
    run_state_dir.mkdir()
    with pytest.raises(PathTraversal):
        probe_ticket_state(run_state_dir, bad_id)


def test_path_traversal_uses_the_uniform_invalid_ticket_id_contract() -> None:
    # run_state and ticket_md must raise the SAME PathTraversal with the uniform
    # ``invalid_ticket_id`` code (per ARCHITECTURE.md), not two divergent classes.
    from factory_console.file_adapter.ticket_md import PathTraversal as TicketMdPathTraversal

    exc = PathTraversal("../etc/passwd")
    assert exc.code == "invalid_ticket_id"
    assert exc.status == 400
    assert PathTraversal is TicketMdPathTraversal


# --------------------------------------------------------------------------- #
# find_run_state_dir — fallback probe order
# --------------------------------------------------------------------------- #


def test_find_run_state_dir_uses_docs_planning_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    fallback.mkdir(parents=True)
    assert find_run_state_dir(tmp_path) == fallback, (
        "with only docs/planning/.run-state present it must be returned"
    )


def test_find_run_state_dir_prefers_factory_when_both_present(tmp_path: Path) -> None:
    primary = tmp_path / ".factory" / "run-state"
    primary.mkdir(parents=True)
    (tmp_path / "docs" / "planning" / ".run-state").mkdir(parents=True)
    assert find_run_state_dir(tmp_path) == primary, (
        "when both locations exist, .factory/run-state must win the fallback order"
    )


def test_find_run_state_dir_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_run_state_dir(tmp_path) is None, (
        "with neither location present, find_run_state_dir must return None"
    )


def test_find_run_state_dir_ignores_a_non_directory_at_primary(tmp_path: Path) -> None:
    # A plain file at the primary path is not a usable run-state dir (is_dir, not
    # exists), so the probe must fall through to the docs/planning location.
    factory = tmp_path / ".factory"
    factory.mkdir()
    (factory / "run-state").write_text("")
    fallback = tmp_path / "docs" / "planning" / ".run-state"
    fallback.mkdir(parents=True)
    assert find_run_state_dir(tmp_path) == fallback, (
        "a non-directory at the primary path must be skipped in favor of the fallback"
    )


# --------------------------------------------------------------------------- #
# is_run_state_marker — the marker-layout rule (shared with the T40 watcher)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rel_path, expected",
    [
        # A marker lives exactly <location>/<state>/<ticket_id> — two segments
        # below either documented run-state location.
        (".factory/run-state/ready/T99", True),
        (".factory/run-state/in-flight/T42", True),
        ("docs/planning/.run-state/ready/T88", True),  # the fallback location
        # Not markers: the bare location (depth 0), a bare <state> dir (depth 1),
        # and something deeper than a marker (depth 3+).
        (".factory/run-state", False),
        (".factory/run-state/ready", False),
        (".factory/run-state/ready/T99/extra", False),
        # Outside any run-state location entirely (planning docs).
        ("docs/planning/tickets/T99.md", False),
        ("README.md", False),
    ],
)
def test_is_run_state_marker_only_true_at_marker_depth(rel_path: str, expected: bool) -> None:
    assert is_run_state_marker(rel_path) is expected


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# (shared with tests/integration/test_real_file_watcher.py via
# tests/_read_only_guard.py)
# --------------------------------------------------------------------------- #


def test_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(run_state_module)


def test_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(run_state_module)
