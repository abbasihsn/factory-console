"""The :class:`Project` domain model — a discovered target project.

Mirrors the ``Project`` entry of ``ARCHITECTURE.md`` data_model. Constructed
once per request; lives in memory only. No I/O here.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Project(BaseModel):
    """A target App Factory project resolved from its filesystem layout."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rootPath: Path
    ticketsManifestPath: Path
    ticketsDir: Path
    roadmapPath: Path | None = None
    runStateDir: Path | None = None
    discoveredAt: datetime
