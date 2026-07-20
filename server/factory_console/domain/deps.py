"""Dependency-neighborhood and roadmap domain models.

Mirrors the ``DepNeighborhood`` / ``Roadmap`` entries of ``ARCHITECTURE.md``
data_model. No I/O here.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.ticket import TicketSummary


class DepNeighborhood(BaseModel):
    """A ticket's direct dependency neighborhood plus unresolved dep ids."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket: TicketSummary
    directDeps: list[TicketSummary] = Field(default_factory=list)
    directDependents: list[TicketSummary] = Field(default_factory=list)
    unresolvedDeps: list[str] = Field(default_factory=list)


class Roadmap(BaseModel):
    """The project roadmap document (presence-only in MVP; full body in v1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    bodyMarkdown: str
    bodyHtml: str
