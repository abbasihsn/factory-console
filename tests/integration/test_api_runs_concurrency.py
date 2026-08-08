"""``GET /api/v1/runs`` does its filesystem work OFF the event loop (T98).

Separate from ``test_api_runs.py`` — which pins the endpoint's CONTENT over the sync
``TestClient`` — because this suite pins something a sync client structurally cannot
observe: that a request stuck in a slow artifact read does not stall the requests
beside it. Proving that needs two genuinely concurrent coroutines against one ASGI app
(``httpx.AsyncClient`` + ``ASGITransport``, the wiring ``ARCHITECTURE.md``'s testing
strategy names for integration tests; the repo runs ``asyncio_mode=auto``, so
``async def test_...`` needs no decorator), and it needs a WALL-CLOCK assertion. A test
that only asserted the offload was configured — that ``anyio.to_thread.run_sync`` is
called, or that both requests eventually answered 200 — passes on the bug it exists to
catch, since a blocked loop still serves both requests, just one after the other.

The blocking is simulated with a real :func:`time.sleep` inside the artifact reader,
because that is exactly the shape of the thing being fixed: a synchronous port doing
syscalls the loop cannot interleave. ``asyncio.sleep`` would be a different (and
already-safe) program.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from factory_console.app import create_app
from factory_console.domain import Project, Ticket
from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter import FakeFileAdapter
from factory_console.file_adapter.run_artifacts import FakeRunArtifactReader

FAKE_ROOT = Path("/factory/demo-project")
"""A root that exists on no disk — the house convention for fake-backed tests."""

SLOW_READ_SECONDS = 0.3
"""How long the simulated disk read blocks. Long enough that a stalled loop is
unambiguous at wall-clock resolution, short enough to stay a fast unit-speed test."""

FAST_BUDGET_SECONDS = 0.1
"""What the concurrent request must finish within, measured from the moment BOTH were
launched. Well under :data:`SLOW_READ_SECONDS`, so a loop that served the two
sequentially cannot satisfy it, and well above the microseconds ``/health`` actually
takes, so a loaded CI box does not flake it."""


class SlowRunArtifactReader(FakeRunArtifactReader):
    """A :class:`FakeRunArtifactReader` whose result reads block on a real sleep.

    Wraps rather than replaces the shipped fake so the RECORDS this endpoint composes
    stay the fake's — only their timing changes. ``entered`` is set at the top of the
    sleep, which is what lets the concurrent request start from a known state: the
    slow request is provably inside the blocking section, not merely dispatched.
    """

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()

    def read_result(self, project: Project, ticket_id: str) -> ArtifactRead:
        """Block for :data:`SLOW_READ_SECONDS`, then answer as the fake would."""
        self.entered.set()
        time.sleep(SLOW_READ_SECONDS)
        return super().read_result(project, ticket_id)


def _app(artifacts: FakeRunArtifactReader) -> FastAPI:
    """The real app over a one-ticket manifest and the given artifact reader."""
    project = Project(
        rootPath=FAKE_ROOT,
        ticketsManifestPath=FAKE_ROOT / "docs/planning/tickets.json",
        ticketsDir=FAKE_ROOT / "docs/planning/tickets",
        roadmapPath=FAKE_ROOT / "ROADMAP.md",
        runStateDir=FAKE_ROOT / ".factory/run-state",
        discoveredAt=datetime(2026, 8, 6, 12, 0, 0),
    )
    ticket = Ticket(
        id="T98",
        title="Ticket T98",
        status="todo",
        track="backend",
        milestone="v2.2",
        filePath=FAKE_ROOT / "docs/planning/tickets/T98.json",
        bodyMarkdown="# T98",
        bodyHtml="<h1>T98</h1>",
        raw={"id": "T98"},
    )
    return create_app(
        FakeFileAdapter(project=project, tickets=[ticket]),
        version="0.0.0",
        project_root=FAKE_ROOT,
        run_artifact_reader=artifacts,
    )


async def test_a_request_is_served_while_runs_is_blocked_in_a_slow_artifact_read() -> None:
    """``/health`` answers in milliseconds while ``/runs`` sits in a 300ms disk read.

    The proof is the wall clock, measured from one shared start: the fast response has
    to arrive long BEFORE the slow one, not merely alongside it. If ``list_runs`` called
    its ports inline again, the loop would be pinned inside ``read_result`` for the whole
    sleep and ``/health`` could not answer until after it — so the fast timing assertion
    is the one that fails on the bug, and it is stated first for that reason.
    """
    artifacts = SlowRunArtifactReader()
    app = _app(artifacts)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        started = time.perf_counter()

        async def slow() -> float:
            response = await client.get("/api/v1/runs")
            assert response.status_code == 200
            assert response.json()["total"] == 1
            return time.perf_counter() - started

        async def fast() -> float:
            # Wait until the slow request is provably INSIDE the blocking read, so this
            # cannot pass by simply finishing before that request got going. On the bug
            # the loop is stalled, so this poll itself does not resume until the read is
            # over — which is what pushes the elapsed time past the budget.
            while not artifacts.entered.is_set():
                await asyncio.sleep(0.005)
            response = await client.get("/api/v1/health")
            assert response.status_code == 200
            return time.perf_counter() - started

        slow_elapsed, fast_elapsed = await asyncio.gather(slow(), fast())

    assert fast_elapsed < FAST_BUDGET_SECONDS, (
        f"a concurrent request took {fast_elapsed:.3f}s while /runs was in a "
        f"{SLOW_READ_SECONDS}s read — the event loop was blocked, not offloaded"
    )
    assert slow_elapsed >= SLOW_READ_SECONDS, (
        "the slow read must really have been slow, or this test proves nothing"
    )
    assert fast_elapsed < slow_elapsed, "the two requests overlapped rather than queued"
