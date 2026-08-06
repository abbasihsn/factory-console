"""Live-update domain model — a single filesystem :class:`ChangeEvent`.

A :class:`ChangeEvent` is the payload the ``FileWatcher`` port streams to the
backend SSE endpoint (``/api/v1/events``, T45): one observed change under the
watched project, tagged with the ``scope`` (planning docs, factory run-state, the
factory spend ledger, or the per-run artefacts) that lets the frontend decide what
to refresh.

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
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# The change-event vocabularies, exported as named types so producers (the T40
# watcher) share ONE source of truth instead of re-listing the verbs/scopes or
# scraping them back out of Pydantic internals. ``ChangeKind`` is the change
# verb; ``ChangeScope`` is the watched subtree a change belongs to.
#
# ``ChangeScope`` names WHAT changed, not which on-disk form stored it: a run-state
# change is ``run-state`` whether it arrived as a marker directory or as
# ``.factory/run-state.json``. ``ledger`` is the factory's spend ledger,
# ``.factory/metrics/ledger.jsonl`` (T95) — a factory artefact the console reads
# (``GET /api/v1/spend``) that is neither planning nor run-state, so folding it into
# either would tell a subscriber something untrue about which pane went stale.
# ``runs`` is the per-run artefacts behind ``GET /api/v1/runs`` — a lane result, its
# receipt, and last stop (T99) — which are likewise read by the console and belong to
# neither of the earlier scopes: a lane finishing is not a run-state transition and
# not a planning edit. The set grows with the artefacts the watcher observes; see
# :data:`~factory_console.domain.watched_artifacts.WATCHED_JSON_ARTIFACTS`.
ChangeKind = Literal["created", "modified", "deleted", "moved"]
ChangeScope = Literal["planning", "run-state", "ledger", "runs"]


class ChangeEvent(BaseModel):
    """One filesystem change observed under the watched project.

    ``kind`` is the change verb; ``path`` is the project-relative path that
    changed (never absolute — see the module security note); ``scope`` names what
    changed (``planning`` docs, factory ``run-state``, the spend ``ledger``, or the
    per-run ``runs`` artefacts); ``at`` is when
    the change was observed. Frozen and ``extra='forbid'`` like the other domain
    models, and JSON-serializable so it round-trips through ``model_dump_json``
    for the SSE wire.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ChangeKind
    path: str
    scope: ChangeScope
    at: datetime

    @field_validator("path")
    @classmethod
    def _reject_absolute_path(cls, value: str) -> str:
        """Enforce the project-relative invariant at the wire boundary.

        The module security note promises ``path`` is never absolute; this pins
        that promise on the schema itself (defense-in-depth, mirroring how
        ``TICKET_ID_PATTERN`` is enforced at the model boundary) so no
        constructor — including the T40 watcher — can leak the host's absolute
        filesystem layout onto the SSE wire. POSIX and Windows absolute forms
        are both rejected.
        """
        if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError("path must be project-relative, never absolute")
        return value
