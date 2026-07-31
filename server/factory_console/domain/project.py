"""The resolved target project that a single request reads through.

A :class:`Project` is constructed once per request by the file-adapter's
``load_project`` from a discovered project root. It carries only resolved paths
plus the discovery timestamp — never file contents, which live in memory only
for the duration of the request.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from factory_console.domain.run_state_source import RunStateSource


class Project(BaseModel):
    """Resolved paths for one App Factory project, discovered per request.

    ``roadmapPath``, ``runStateDir`` and ``runStateSource`` are ``None`` when the
    corresponding file or directory is absent from the target project (all are
    optional in the project layout).

    ``runStateSource`` is the resolved run-state artifact — the factory's
    ``.factory/run-state.json`` OR a legacy marker directory — and is what
    run-state reads dispatch on. ``runStateDir`` keeps its original meaning
    exactly: a path ONLY when the resolved source is a directory, so a
    JSON-sourced project has ``runStateDir is None``. Read run-state through
    ``runStateSource``; ``runStateDir`` answers only "which directory, if any, is
    off-limits to the writer".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rootPath: Path
    ticketsManifestPath: Path
    ticketsDir: Path
    roadmapPath: Path | None = None
    runStateDir: Path | None = None
    runStateSource: RunStateSource | None = None
    discoveredAt: datetime

    @model_validator(mode="before")
    @classmethod
    def _source_defaults_to_the_run_state_dir(cls, data: Any) -> Any:
        """Fill an omitted ``runStateSource`` from ``runStateDir``, if one was given.

        The two fields describe the same fact and MUST NOT contradict each other.
        A caller that supplies only ``runStateDir`` (as every pre-``runStateSource``
        construction site does) means "this project's run-state is that marker
        directory" — leaving ``runStateSource`` ``None`` would make every
        source-aware read answer ``unknown`` and, at the write gate, wave through
        edits to tickets a factory lane owns. Deriving the directory source here
        keeps the older two-field-free construction honest; a caller that supplies
        ``runStateSource`` explicitly (``load_project``) is never second-guessed.
        """
        if not isinstance(data, dict):
            return data
        if data.get("runStateSource") is None and data.get("runStateDir") is not None:
            return {
                **data,
                "runStateSource": RunStateSource(kind="directory", path=data["runStateDir"]),
            }
        return data
