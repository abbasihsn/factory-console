"""The resolved target project that a single request reads through.

A :class:`Project` is constructed once per request by the file-adapter's
``load_project`` from a discovered project root. It carries only resolved paths
plus the discovery timestamp — never file contents, which live in memory only
for the duration of the request.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Project(BaseModel):
    """Resolved paths for one App Factory project, discovered per request.

    ``roadmapPath`` and ``runStateDir`` are ``None`` when the corresponding file
    or directory is absent from the target project (both are optional in the
    project layout).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rootPath: Path
    ticketsManifestPath: Path
    ticketsDir: Path
    roadmapPath: Path | None = None
    runStateDir: Path | None = None
    discoveredAt: datetime
