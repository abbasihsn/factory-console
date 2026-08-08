"""Two concurrent selection switches leave the console internally consistent.

Separate from ``test_api_projects.py`` — which pins the write routes' CONTENT over the
sync ``TestClient`` — because this pins something a sync client structurally cannot
observe. ``SelectionState.select`` is atomic, but no HTTP caller may use it: its
registry round trip blocks and its on-change hook needs the event loop, so
``api/v1/projects.py`` runs ``_resolve_and_persist`` off-loop and ``_apply_selected``
back on it. A switch therefore SPANS an ``await``, and the loop stops serialising it
the way it serialises an ordinary handler.

Left unguarded, two switches in flight can persist in one order and apply in the
other, so the in-memory selection (and the watcher the on-change hook targets) ends up
naming a project the registry no longer records as selected — a divergence that
survives until the process restarts. ``app.state.selection_lock`` closes it, and this
suite is the proof: remove the ``async with selection_lock`` from ``select_current``
and ``test_concurrent_switches_leave_memory_and_registry_agreeing`` fails.

Reproducing it needs a real interleaving, not a simulated one. The slow registry below
sleeps with :func:`time.sleep` AFTER its write lands, because that is the exact shape
of the hazard: a synchronous port doing a syscall on a worker thread while the loop
runs the other request's handler. ``asyncio.sleep`` would be a different program.
The repo runs ``asyncio_mode=auto``, so ``async def test_...`` needs no decorator.

**``remove_project`` is guarded by the same lock but is NOT regression-tested here.**
It performs the same two-phase clear and takes the same ``selection_lock``, so the fix
covers it. A test was attempted and deleted rather than kept: every interleaving that
could be driven through the HTTP surface passed with the lock removed, so it asserted
an invariant it could not actually falsify, which is worse than no test — it reads as
coverage. Removing the selected project narrows the window itself, because the delete's
``ON DELETE SET NULL`` has already cleared the persisted selection before the handler's
clear runs. If someone finds a driving order that does diverge, this is the file for it.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from factory_console.app import create_app
from factory_console.config import WRITE_TOKEN_HEADER
from factory_console.domain import Project
from factory_console.domain.registry import RegisteredProject
from factory_console.file_adapter import FakeFileAdapter
from factory_console.store.fake_registry import FakeProjectRegistry

PINNED_TOKEN = "test-token"
CURRENT_ROUTE = "/api/v1/projects/current"

SLOW_PERSIST_SECONDS = 0.3
"""Long enough that the fast switch completes inside the slow one's window.

Only a LOWER bound matters: the fast request must be able to run its whole handler
while the slow one sits in ``time.sleep`` on a worker thread. Nothing asserts an
upper bound, so a loaded CI box makes this slower, never flaky.
"""


class SlowSelectRegistry(FakeProjectRegistry):
    """A registry whose ``set_selected_project`` sleeps AFTER writing, for one id.

    After, not before, and that ordering is the whole point. The write must LAND so
    the two requests' persists complete in a known order; the sleep then holds the
    slow request inside its worker-thread hop, giving the fast one a window to run its
    own persist AND its in-memory apply. Sleeping first would just serialise the two
    writes and reproduce nothing.
    """

    _UNSET = object()

    def __init__(self, *, seconds: float) -> None:
        # Set BEFORE super().__init__(), which calls set_selected_project itself.
        self.slow_target: object = self._UNSET
        self._seconds = seconds
        super().__init__()

    def slow_down(self, project_id: str | None) -> None:
        """Slow persists naming ``project_id``, which may be ``None``.

        ``None`` is the CLEAR that ``remove_project`` performs, and slowing it is the
        only way to reproduce the removal race: the two handlers must be held apart at
        different points, or the faster one simply finishes before the slower one has
        anything to interleave with.
        """
        self.slow_target = project_id

    def set_selected_project(self, project_id: str | None) -> RegisteredProject | None:
        selected = super().set_selected_project(project_id)
        if self.slow_target is not self._UNSET and project_id == self.slow_target:
            time.sleep(self._seconds)
        return selected


def _app(registry: FakeProjectRegistry, project_root: Path) -> FastAPI:
    """A real app over a fake adapter, pinned at ``project_root``."""
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        discoveredAt=datetime(2026, 1, 1),
    )
    return create_app(
        FakeFileAdapter(project=project, tickets=[]),
        version="0.0.0",
        project_root=project_root,
        project_registry=registry,
        write_token=PINNED_TOKEN,
    )


async def _switch(client: AsyncClient, project_id: str) -> int:
    """PUT the selection to ``project_id`` and return the status code."""
    response = await client.put(
        CURRENT_ROUTE,
        json={"projectId": project_id},
        headers={WRITE_TOKEN_HEADER: PINNED_TOKEN},
    )
    return response.status_code


async def test_concurrent_switches_leave_memory_and_registry_agreeing(
    tmp_path: Path,
) -> None:
    """The in-memory selection names whatever the registry last persisted.

    The assertion is deliberately about AGREEMENT rather than about which project
    wins. Two switches racing is a genuine tie — either may land last, and both
    answers are correct — so pinning a winner would pin the scheduler instead of the
    invariant. What must never happen is the two disagreeing.
    """
    slow_dir = tmp_path / "slow"
    fast_dir = tmp_path / "fast"
    slow_dir.mkdir()
    fast_dir.mkdir()

    # The slow id is not known until the row exists, so the registry is told which id
    # to slow down only once it has handed one out.
    registry = SlowSelectRegistry(seconds=SLOW_PERSIST_SECONDS)
    slow = registry.add_project(slow_dir, "slow")
    fast = registry.add_project(fast_dir, "fast")
    registry.slow_down(slow.id)

    app = _app(registry, tmp_path / "pinned")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        # The slow switch starts first and is still inside its worker-thread sleep
        # when the fast one begins, which is what puts two switches in flight.
        slow_call = asyncio.create_task(_switch(client, slow.id))
        await asyncio.sleep(0)
        fast_call = asyncio.create_task(_switch(client, fast.id))
        statuses = await asyncio.gather(slow_call, fast_call)

    assert statuses == [200, 200]

    persisted = registry.get_selected_project()
    assert persisted is not None
    in_memory = app.state.selection.current_id()
    assert in_memory == persisted.id, (
        f"in-memory selection {in_memory!r} diverged from the persisted "
        f"{persisted.id!r}: the two-phase switch interleaved"
    )
