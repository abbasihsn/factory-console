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

import ast
import asyncio
import inspect
import threading
from pathlib import Path

import pytest

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

        async def _drain_until_dest() -> ChangeEvent:
            pending: asyncio.Task | None = first
            while True:
                event = await (pending if pending is not None else stream.__anext__())
                pending = None
                # Skip the temp file's own create event; the destination path is
                # the assertion. (A backend that reports the rename as a plain
                # create of the destination also satisfies this.)
                if event.path == "docs/planning/tickets/T50.md":
                    return event

        event = await asyncio.wait_for(_drain_until_dest(), _EVENT_TIMEOUT)
        assert event.path == "docs/planning/tickets/T50.md"
        assert event.scope == "planning"
    finally:
        first.cancel()
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
    # Neither root exists — start still works and stops cleanly (nothing scheduled).
    watcher = RealFileWatcher(tmp_path)
    watcher.start()
    try:
        assert watcher._observer is not None
    finally:
        watcher.stop()


def test_real_watcher_satisfies_runtime_checkable_file_watcher(tmp_path: Path) -> None:
    assert isinstance(RealFileWatcher(tmp_path), FileWatcher)


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# (mirrors tests/unit/test_run_state.py)
# --------------------------------------------------------------------------- #

_FORBIDDEN_ATTR_CALLS = frozenset(
    {
        "write_text",
        "write_bytes",
        "touch",
        "mkdir",
        "rmdir",
        "unlink",
        "rename",
        "replace",
        "makedirs",
        "remove",
    }
)
_FORBIDDEN_OPEN_MODE_CHARS = frozenset("wax+")

_READ_ONLY_HEADER = "# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests."


def _module_source() -> str:
    """Return the on-disk source text of the watcher_real module under test."""
    source_file = inspect.getsourcefile(watcher_real)
    assert source_file is not None, "could not locate watcher_real.py source on disk"
    return Path(source_file).read_text()


def _open_mode_arg(call: ast.Call) -> ast.expr | None:
    """Return the ``mode`` argument node of an ``open(...)`` call, if given."""
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


def test_module_source_has_no_filesystem_mutation() -> None:
    tree = ast.parse(_module_source())
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTR_CALLS:
            violations.append(f"{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = _open_mode_arg(node)
            if (
                isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and set(mode.value) & _FORBIDDEN_OPEN_MODE_CHARS
            ):
                violations.append(f"open(mode={mode.value!r}) at line {node.lineno}")
    assert not violations, (
        "watcher_real.py must be read-only but contains mutation calls: " + ", ".join(violations)
    )


def test_module_source_carries_the_read_only_header() -> None:
    assert _READ_ONLY_HEADER in _module_source(), (
        "watcher_real.py must carry the literal READ-ONLY header comment"
    )
