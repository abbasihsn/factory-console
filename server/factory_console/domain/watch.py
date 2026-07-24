"""Live-update domain model — a single filesystem :class:`ChangeEvent`.

A :class:`ChangeEvent` is the payload the ``FileWatcher`` port streams to the
backend SSE endpoint (``/api/v1/events``, T45): one observed change under the
watched project, tagged with the ``scope`` (planning docs vs factory run-state)
that lets the frontend decide what to refresh.

Security note — ``path`` is ALWAYS project-relative, never absolute. A watcher
never discloses the host's filesystem layout: the real (watchdog-backed)
implementation in T40 relativizes every observed path against the project root
before constructing a :class:`ChangeEvent`, and this contract is what the SSE
stream leans on. It is imported by full path from its consumers and deliberately
NOT re-exported from ``domain/__init__`` — SSE events are streamed, not part of
the request ``response_model`` set, so the aggregation file stays collision-free
across the parallel v1 tickets.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ChangeEvent(BaseModel):
    """One filesystem change observed under the watched project.

    ``kind`` is the change verb; ``path`` is the project-relative path that
    changed (never absolute — see the module security note); ``scope`` names the
    watched subtree (``planning`` docs or factory ``run-state``); ``at`` is when
    the change was observed. Frozen and ``extra='forbid'`` like the other domain
    models, and JSON-serializable so it round-trips through ``model_dump_json``
    for the SSE wire.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["created", "modified", "deleted", "moved"]
    path: str
    scope: Literal["planning", "run-state"]
    at: datetime
