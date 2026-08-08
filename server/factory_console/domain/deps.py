"""Dependency-neighborhood and roadmap domain models.

A :class:`DepNeighborhood` is the payload for a ticket's dependency view; its
dependents are computed per request by reverse-indexing ``dependsOn`` across the
manifest. A :class:`Roadmap` models the project roadmap document (presence is
detected in the MVP; the full body is rendered in later milestones).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from factory_console.domain.run_state import RunState
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


class RoadmapItem(BaseModel):
    """A single milestone list-item parsed from ``ROADMAP.md``.

    ``text`` is the cleaned item label (marker and any leading checkbox stripped);
    ``ticketId`` is the item's linked ticket id when one is present, else ``None``.

    **``runState`` REPLACED a ``done`` flag read off the item's own checkbox, and the
    change is about where the truth lives.** A committed ``[x]`` is derived state in a
    hand-maintained file: it is a claim about the factory that nobody verified, it goes
    stale the moment a lane merges, and App Factory v3 §4 forbids it outright —
    ``factory-doctor`` FAILs a repository that carries one. So the checkbox is now
    stripped from the label and its mark discarded, and the status comes from the same
    run-state source the ticket list and the write gate already read. Two views of one
    ticket cannot disagree when one source answers both.

    ``None`` means **this item names no ticket**, and it is not the same as
    :attr:`~factory_console.domain.run_state.RunState.unknown`. ``unknown`` is an answer
    — a source was consulted and said nothing about this id — while ``None`` is the
    absence of a question: a prose bullet has no status because there is nothing to have
    one. Collapsing them would badge every section header and narrative line as a ticket
    the factory has never heard of.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    ticketId: str | None = None
    runState: RunState | None = None


class RoadmapMilestone(BaseModel):
    """A ``## `` heading from ``ROADMAP.md`` with its list of items."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    items: list[RoadmapItem] = Field(default_factory=list)


class Roadmap(BaseModel):
    """The project roadmap document (``ROADMAP.md``).

    ``milestones`` is the structured breakdown parsed from the body — empty for a
    body with no ``## `` headings — leaving ``bodyMarkdown``/``bodyHtml`` as the
    full document for verbatim rendering.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    bodyMarkdown: str
    bodyHtml: str
    milestones: list[RoadmapMilestone] = Field(default_factory=list)
