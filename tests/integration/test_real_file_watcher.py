"""Integration tests for the watchdog-backed :class:`RealFileWatcher`.

Unlike the deterministic ``FakeFileWatcher`` unit tests, these exercise the real
component end-to-end against a ``tmp_path`` project: a live ``watchdog.Observer``
thread, real filesystem writes, and the thread→loop→asyncio-queue fan-out. Real
watchdog events are asynchronous and slower than the fake, so every wait uses a
generous ``asyncio.wait_for`` timeout and we poll for events rather than assuming
fixed timing; the only bounded ``sleep`` closes the debounce window and is
commented as such.

``pytest.importorskip`` skips the whole module cleanly if watchdog is ever
absent. The repo runs ``asyncio_mode=auto`` so ``async def test_...`` needs no
decorator.
"""

import asyncio
import threading
from pathlib import Path

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

pytest.importorskip("watchdog")

from factory_console.domain.watch import ChangeEvent  # noqa: E402
from factory_console.file_adapter import watcher_real  # noqa: E402
from factory_console.file_adapter.watcher import FileWatcher  # noqa: E402
from factory_console.file_adapter.watcher_real import RealFileWatcher  # noqa: E402

# Real FS events are slower than the fake; keep timeouts generous so a slow CI
# runner does not flake, but bounded so a genuine hang still fails fast.
_EVENT_TIMEOUT = 5.0
# A short window in which we assert NO event arrives (outside-root / no-second-
# event cases). Comfortably longer than the debounce window.
_QUIET_WINDOW = 0.6


def _make_project(root: Path) -> None:
    """Create the two watched roots so the observer has something to schedule."""
    (root / "docs" / "planning" / "tickets").mkdir(parents=True)
    (root / ".factory" / "run-state" / "ready").mkdir(parents=True)


async def _primed_stream(watcher: RealFileWatcher) -> tuple[object, asyncio.Task]:
    """Subscribe and prime the first ``__anext__`` so the queue is registered.

    Events only reach currently-registered subscribers, so the queue must exist
    before any write. Returns the async generator and the pending first-event
    task.
    """
    stream = watcher.subscribe()
    task = asyncio.ensure_future(stream.__anext__())
    # Let the generator run up to its first ``await queue.get()`` (registers the
    # queue). A single loop turn is enough — no timing assumption.
    await asyncio.sleep(0)
    return stream, task


async def _next_event(stream: object, task: asyncio.Task | None = None) -> ChangeEvent:
    """Await the next event, reusing a primed first-event task if given."""
    pending = task if task is not None else asyncio.ensure_future(stream.__anext__())
    return await asyncio.wait_for(pending, _EVENT_TIMEOUT)


async def _drain_until(stream: object, path: str, task: asyncio.Task | None = None) -> ChangeEvent:
    """Await events until one names ``path``, discarding the rest.

    For the rename cases, where the interesting event is not necessarily the first:
    a backend may also report the temp file's own create, and which events precede
    the one under test is a backend detail no assertion should depend on. Reuses the
    primed first-event task the same way :func:`_next_event` does, then falls through
    to fresh ``__anext__`` calls. Bounded by ``_EVENT_TIMEOUT`` overall, so a backend
    that never reports ``path`` fails instead of hanging.
    """

    async def _drain() -> ChangeEvent:
        pending = task
        while True:
            event = await (pending if pending is not None else stream.__anext__())
            pending = None
            if event.path == path:
                return event

    return await asyncio.wait_for(_drain(), _EVENT_TIMEOUT)


async def test_write_under_each_root_emits_scoped_relative_event(tmp_path: Path) -> None:
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        ticket = tmp_path / "docs" / "planning" / "tickets" / "T99.md"
        ticket.write_text("# T99\n")
        planning_event = await _next_event(stream, first)
        assert planning_event.scope == "planning"
        assert planning_event.path == "docs/planning/tickets/T99.md"
        assert planning_event.kind in {"created", "modified"}

        marker = tmp_path / ".factory" / "run-state" / "ready" / "T99"
        marker.write_text("")
        run_state_event = await _next_event(stream)
        assert run_state_event.scope == "run-state"
        assert run_state_event.path == ".factory/run-state/ready/T99"
        assert run_state_event.kind in {"created", "modified"}
    finally:
        await stream.aclose()
        watcher.stop()


async def test_run_state_fallback_location_is_scoped_run_state(tmp_path: Path) -> None:
    # run_state.find_run_state_dir documents TWO run-state locations; the fallback
    # docs/planning/.run-state lives UNDER docs/planning, so it is observed by the
    # recursive planning watch. A marker there must still be scoped "run-state"
    # (not "planning"), or the SSE client would refresh the wrong pane.
    (tmp_path / "docs" / "planning" / ".run-state" / "ready").mkdir(parents=True)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        marker = tmp_path / "docs" / "planning" / ".run-state" / "ready" / "T88"
        marker.write_text("")
        event = await _next_event(stream, first)
        assert event.scope == "run-state"
        assert event.path == "docs/planning/.run-state/ready/T88"
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_run_state_directory_marker_emits_run_state_event(tmp_path: Path) -> None:
    # A run-state marker can itself be a DIRECTORY (run_state resolves a
    # <state>/<ticket_id> marker as a file OR a directory), and states like
    # in-flight/ready commonly use directory markers. Watchdog reports creating
    # one as a directory event; it must still emit a run-state ChangeEvent, or
    # those transitions never reach the SSE client and the badge never updates.
    _make_project(tmp_path)
    # Pre-create the state dir so only the marker's own creation fires post-subscribe.
    (tmp_path / ".factory" / "run-state" / "in-flight").mkdir()
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        marker = tmp_path / ".factory" / "run-state" / "in-flight" / "T55"
        marker.mkdir()  # a directory marker, not a file
        event = await _next_event(stream, first)
        assert event.scope == "run-state"
        assert event.path == ".factory/run-state/in-flight/T55"
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_bare_state_directory_creation_emits_nothing(tmp_path: Path) -> None:
    # The marker-depth guard must SUPPRESS run-state directory events that are
    # NOT <state>/<ticket_id> markers — the run-state root and a bare <state>
    # dir (one segment below the root) are the levels macOS FSEvents replays
    # spuriously. Creating a fresh bare <state> dir must yield no ChangeEvent,
    # or those replays would trigger phantom pane refreshes. (This is the
    # negative half of test_run_state_directory_marker_emits_run_state_event.)
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        (tmp_path / ".factory" / "run-state" / "merged").mkdir()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first, _QUIET_WINDOW)
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_rapid_burst_to_same_path_debounces_to_one_event(tmp_path: Path) -> None:
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        ticket = tmp_path / "docs" / "planning" / "tickets" / "T42.md"
        # A tight burst of writes to the SAME path — all well within the 150ms
        # debounce window, so they must coalesce into a single ChangeEvent.
        for n in range(6):
            ticket.write_text(f"# T42 rev {n}\n")

        event = await _next_event(stream, first)
        assert event.path == "docs/planning/tickets/T42.md"

        # No second event for the coalesced burst.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(stream.__anext__(), _QUIET_WINDOW)
    finally:
        await stream.aclose()
        watcher.stop()


async def test_atomic_rename_within_root_reports_destination_path(tmp_path: Path) -> None:
    # An atomic editor save (write temp, then rename onto the real name) reaches
    # watchdog as a ``moved`` event whose ``src_path`` is the temp file and whose
    # ``dest_path`` is the ticket. The emitted ChangeEvent must name the
    # destination (the file that actually changed), never the vanished temp file.
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        tickets = tmp_path / "docs" / "planning" / "tickets"
        temp = tickets / ".T50.md.tmp"
        temp.write_text("# T50\n")
        temp.rename(tickets / "T50.md")

        # Skip the temp file's own create event; the destination path is the
        # assertion. (A backend that reports the rename as a plain create of the
        # destination also satisfies this.)
        event = await _drain_until(stream, "docs/planning/tickets/T50.md", first)
        assert event.path == "docs/planning/tickets/T50.md"
        assert event.scope == "planning"
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_json_run_state_source_write_emits_run_state_event(tmp_path: Path) -> None:
    # T91/F1: since T78 the PRIMARY run-state source is a FILE,
    # ``.factory/run-state.json``. The watcher used to schedule only the
    # DIRECTORY locations, so on a JSON-sourced project a run-state change fired
    # no event at all and the live-update path was silently dead. Writing the
    # file must deliver a ChangeEvent scoped exactly like a directory-source one.
    (tmp_path / "docs" / "planning").mkdir(parents=True)
    (tmp_path / ".factory").mkdir()
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        (tmp_path / ".factory" / "run-state.json").write_text('{"version": 1, "tickets": {}}')
        event = await _next_event(stream, first)
        assert event.scope == "run-state"
        assert event.path == ".factory/run-state.json"
        assert event.kind in {"created", "modified"}
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_json_run_state_source_atomic_rename_emits_event(tmp_path: Path) -> None:
    # The factory REPLACES run-state.json via mktemp + mv (INV-03), so the file's
    # inode changes on every update. A naive single-file watch would follow the
    # old inode and go quiet after the first rename; watching the parent
    # directory and filtering by name is what keeps this firing.
    (tmp_path / ".factory").mkdir()
    factory_dir = tmp_path / ".factory"
    (factory_dir / "run-state.json").write_text('{"version": 1, "tickets": {}}')
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        temp = factory_dir / "run-state.json.tmp"
        temp.write_text('{"version": 1, "tickets": {"T91": {"status": "merged"}}}')
        temp.rename(factory_dir / "run-state.json")

        event = await _drain_until(stream, ".factory/run-state.json", first)
        assert event.scope == "run-state"
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_other_files_beside_the_json_source_yield_nothing(tmp_path: Path) -> None:
    # Watching ``.factory`` is a means to observe ONE file. T91 explicitly must
    # not widen scope: any other entry under ``.factory`` stays invisible.
    (tmp_path / ".factory").mkdir()
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        (tmp_path / ".factory" / "ledger.jsonl").write_text("{}\n")
        (tmp_path / ".factory" / "notes").mkdir()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first, _QUIET_WINDOW)
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_ledger_append_emits_ledger_event(tmp_path: Path) -> None:
    # T95: the spend ledger (``.factory/metrics/ledger.jsonl``, read by
    # ``GET /api/v1/spend`` since T79) was READ but never WATCHED — T91 generalized
    # nothing, so it fixed run-state alone and left the next artifact in exactly the
    # condition it had just repaired. An APPEND to the ledger must deliver a
    # ChangeEvent all the way through the real observer and the subscriber fan-out;
    # asserting the watcher was merely configured would not have caught the original
    # bug either.
    (tmp_path / ".factory" / "metrics").mkdir(parents=True)
    ledger = tmp_path / ".factory" / "metrics" / "ledger.jsonl"
    ledger.write_text('{"ticket_id": "T94"}\n')
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write('{"ticket_id": "T95"}\n')
        event = await _next_event(stream, first)
        assert event.scope == "ledger"
        assert event.path == ".factory/metrics/ledger.jsonl"
        assert event.kind in {"created", "modified"}
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_ledger_atomic_rename_emits_event(tmp_path: Path) -> None:
    # The INV-03 trap applies to the ledger exactly as it does to run-state: the
    # factory replaces files via mktemp + mv, so the inode changes and a naive
    # single-file watch goes quiet after the first update. The ledger's parent
    # ``.factory/metrics`` is watched instead, and names — not inodes — are matched.
    (tmp_path / ".factory" / "metrics").mkdir(parents=True)
    metrics_dir = tmp_path / ".factory" / "metrics"
    (metrics_dir / "ledger.jsonl").write_text('{"ticket_id": "T94"}\n')
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        temp = metrics_dir / "ledger.jsonl.tmp"
        temp.write_text('{"ticket_id": "T94"}\n{"ticket_id": "T95"}\n')
        temp.rename(metrics_dir / "ledger.jsonl")

        event = await _drain_until(stream, ".factory/metrics/ledger.jsonl", first)
        assert event.scope == "ledger"
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_nested_content_under_the_json_parent_yields_nothing(tmp_path: Path) -> None:
    # The sibling test above covers DIRECT children of ``.factory``. This one covers
    # DEPTH, which is a different guard: each file artifact's parent is scheduled
    # ``recursive=False`` precisely so nothing below it is ever reported, and the
    # handler's json-only drop matches on the immediate parent only. A nested write
    # therefore has to be invisible at the SCHEDULING layer — if one of those watches
    # is ever flipped to recursive, this event would reach the handler, miss both the
    # exact-path match and the direct-parent drop, and be dispatched as ``planning``:
    # scope widening the ticket forbids, and a refresh of the wrong pane.
    #
    # ``.factory/metrics/`` used to be the example of that nested content, and since
    # T95 it is a watched parent in its own right — so the depth this test guards is
    # now one level BELOW it. The guard itself is unchanged and is the regression this
    # test exists to catch; the ledger file's own event has moved to
    # ``test_ledger_append_emits_ledger_event``.
    (tmp_path / ".factory" / "metrics").mkdir(parents=True)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        (tmp_path / ".factory" / "metrics" / "deep").mkdir()
        (tmp_path / ".factory" / "metrics" / "deep" / "ledger.jsonl").write_text("{}\n")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first, _QUIET_WINDOW)
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_both_run_state_sources_present_do_not_double_fire(tmp_path: Path) -> None:
    # With BOTH a run-state directory and the JSON file, one logical change must
    # still yield exactly one ChangeEvent — the ``.factory`` watch (scheduled for
    # the JSON file) and the recursive ``.factory/run-state`` watch must not each
    # report the same change.
    _make_project(tmp_path)
    (tmp_path / ".factory" / "run-state.json").write_text('{"version": 1, "tickets": {}}')
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        (tmp_path / ".factory" / "run-state.json").write_text(
            '{"version": 1, "tickets": {"T91": {"status": "ready"}}}'
        )
        event = await _next_event(stream, first)
        assert event.path == ".factory/run-state.json"
        assert event.scope == "run-state"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(stream.__anext__(), _QUIET_WINDOW)
    finally:
        await stream.aclose()
        watcher.stop()


async def test_run_state_json_and_ledger_are_scoped_independently(tmp_path: Path) -> None:
    # Two DISTINCT json-only parents are now scheduled (``.factory`` for
    # run-state.json, ``.factory/metrics`` for ledger.jsonl), and ``.factory/metrics``
    # is nested inside the other. Each file must fire once, under its OWN scope: a
    # ledger append tagged ``run-state`` (or vice versa) would refresh the wrong pane,
    # and a double-fire would mean the nested parent got scheduled twice.
    (tmp_path / ".factory" / "metrics").mkdir(parents=True)
    (tmp_path / ".factory" / "run-state.json").write_text('{"version": 1, "tickets": {}}')
    ledger = tmp_path / ".factory" / "metrics" / "ledger.jsonl"
    ledger.write_text('{"ticket_id": "T94"}\n')
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        (tmp_path / ".factory" / "run-state.json").write_text(
            '{"version": 1, "tickets": {"T95": {"status": "ready"}}}'
        )
        event = await _next_event(stream, first)
        assert event.path == ".factory/run-state.json"
        assert event.scope == "run-state"

        # No quiet-window check between the two writes: cancelling an ``__anext__``
        # closes the generator, so the stream would be dead before the ledger write.
        # The ledger event arriving NEXT is itself the non-duplication assertion —
        # a second run-state event would be picked up here and fail the path check.
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write('{"ticket_id": "T95"}\n')
        event = await _next_event(stream)
        assert event.path == ".factory/metrics/ledger.jsonl"
        assert event.scope == "ledger"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(stream.__anext__(), _QUIET_WINDOW)
    finally:
        await stream.aclose()
        watcher.stop()


async def test_change_outside_watched_roots_yields_nothing(tmp_path: Path) -> None:
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        # Under the project root but outside BOTH watched subtrees.
        (tmp_path / "README.md").write_text("nope\n")
        (tmp_path / "docs" / "other.md").write_text("also nope\n")

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first, _QUIET_WINDOW)
    finally:
        first.cancel()
        await stream.aclose()
        watcher.stop()


async def test_stop_joins_the_observer_thread_cleanly(tmp_path: Path) -> None:
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    observer = watcher._observer
    assert observer is not None
    assert observer.is_alive()

    watcher.stop()

    assert not observer.is_alive(), "observer thread must be joined by stop()"
    assert watcher._observer is None
    # No lingering watchdog observer thread survives.
    assert observer not in threading.enumerate()


async def test_dispatch_queued_before_stop_fires_nothing_after_stop(tmp_path: Path) -> None:
    # A dispatch the watchdog thread hands to the loop via call_soon_threadsafe
    # just before the observer stops can still be sitting in the loop's callback
    # queue when stop() returns. Once stopped, that late _coalesce must no-op —
    # not re-arm a timer that flushes a ChangeEvent after shutdown.
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    stream, first = await _primed_stream(watcher)
    try:
        watcher.stop()
        # Simulate the already-queued cross-thread callback landing post-stop.
        watcher._coalesce("modified", "planning", "docs/planning/tickets/T77.md")
        assert watcher._timers == {}
        assert watcher._pending == {}
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(first, _QUIET_WINDOW)
    finally:
        first.cancel()
        await stream.aclose()


def test_stop_is_safe_when_never_started(tmp_path: Path) -> None:
    watcher = RealFileWatcher(tmp_path)
    watcher.stop()  # must not raise
    watcher.stop()  # idempotent


async def test_start_is_idempotent(tmp_path: Path) -> None:
    _make_project(tmp_path)
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    observer = watcher._observer
    watcher.start()  # second call must not swap in a new observer
    try:
        assert watcher._observer is observer
    finally:
        watcher.stop()


async def test_start_is_safe_when_no_watched_root_exists(tmp_path: Path) -> None:
    # No root exists — not the planning dir, not the run-state dir, and not the
    # ``.factory`` parent of the JSON source — so start still works and stops
    # cleanly with NOTHING scheduled (T91 must not invent a root that is absent).
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    try:
        assert watcher._observer is not None
        assert watcher._observer.emitters == set()
    finally:
        watcher.stop()


def test_real_watcher_satisfies_runtime_checkable_file_watcher(tmp_path: Path) -> None:
    assert isinstance(RealFileWatcher(tmp_path), FileWatcher)


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# (shared with tests/unit/test_run_state.py via tests/_read_only_guard.py)
# --------------------------------------------------------------------------- #


def test_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(watcher_real)


def test_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(watcher_real)
