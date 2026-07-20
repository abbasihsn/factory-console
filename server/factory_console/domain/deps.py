"""Dependency-neighborhood and roadmap domain models.

A :class:`DepNeighborhood` is the payload for a ticket's dependency view; its
dependents are computed per request by reverse-indexing ``dependsOn`` across the
manifest. A :class:`Roadmap` models the project roadmap document (presence is
detected in the MVP; the full body is rendered in later milestones).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.ticket import TicketSummary


class DepNeighborhood(BaseModel):
    """A ticket with its direct dependency edges and any unresolved dep ids.

    ``unresolvedDeps`` holds ids listed in the ticket's ``dependsOn`` that have
    no matching ticket in the manifest.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket: TicketSummary
    directDeps: list[TicketSummary] = Field(default_factory=list)
    directDependents: list[TicketSummary] = Field(default_factory=list)
    unresolvedDeps: list[str] = Field(default_factory=list)


class Roadmap(BaseModel):
    """The project roadmap document (``ROADMAP.md``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    bodyMarkdown: str
    bodyHtml: str
