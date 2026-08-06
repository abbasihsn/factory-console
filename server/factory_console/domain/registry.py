"""Registry vocabulary: a tracked project row, and what the console knows about it.

v3.0 turns the console from a single-project viewer into a multi-project read
plane, and this module is the shared vocabulary that turn needs: what a registry
row IS (:class:`RegisteredProject`), and what the console is currently allowed to
say about the path that row names (:data:`RegistryEntryCondition`, projected
together as :class:`RegistryEntry`).

**Why :class:`RegisteredProject` and :class:`~factory_console.domain.project.Project`
are NOT merged.** They have different lifetimes and different truth conditions.
A :class:`RegisteredProject` is a durable console-DB row asserting only "the user
asked this console to track a project at this path, under this name, since this
instant". A :class:`~factory_console.domain.project.Project` is a per-request
resolution of a project's files, constructed by ``FileAdapter.load_project`` and
discarded at the end of the request. Persisting the second would be persisting a
claim about the filesystem that goes stale the moment a file moves — the exact
failure the condition union exists to name honestly. Merging them would also push
a console-owned writable identity into the entity every service and every fake
already takes. Resolution flows one way only: :attr:`RegisteredProject.path` →
discovery → :class:`~factory_console.domain.project.Project`.

:data:`RegistryEntryCondition` is EXHAUSTIVE: it is the single source of truth
the SPA's generated types derive from (via OpenAPI), so its members are the
complete set of answers the console can give about a registered path. Adding a
member is a contract change for the frontend's label map, not an implementation
detail — ``tests/unit/test_domain_registry.py`` pins the membership so a silent
addition fails loudly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

REGISTERED_PROJECT_ID_PATTERN = r"^[0-9a-f]{32}$"
"""Canonical registry-id regex — the single source of truth for id validation.

A registry id is a uuid4 in bare hex form (``uuid4().hex``): exactly 32 lowercase
hex digits, no dashes and no surrounding whitespace. Enforced here at the
Pydantic model boundary via :data:`RegisteredProjectId`, and imported verbatim by
the API boundary's path params and by the store rather than re-spelled — the same
single-definition rule
:data:`~factory_console.domain.ticket.TICKET_ID_PATTERN` sets for ticket ids.
Because it admits no path separators and no ``.``, an id can never name a parent
directory. Downstream imports this constant verbatim, so it must not be narrowed
here.
"""

RegisteredProjectId = Annotated[str, StringConstraints(pattern=REGISTERED_PROJECT_ID_PATTERN)]
"""A registry id constrained to :data:`REGISTERED_PROJECT_ID_PATTERN`."""

RegistryEntryCondition = Literal[
    "ok", "path_missing", "not_a_project", "unreadable", "no_factory_dir"
]
"""What the console currently knows about a registered project's path.

- ``ok`` — the path resolves to a readable App Factory project with everything
  the console reads through: nothing is degraded.
- ``path_missing`` — nothing exists at the registered path any more. The row is
  still a true record of what the user asked for; the target moved or was
  deleted.
- ``not_a_project`` — the path exists and IS readable, and what is there is not
  an App Factory project (no tickets manifest to discover). The console looked
  and found nothing.
- ``unreadable`` — the console could not look. The path could not be read at all
  (permission denied, an I/O error) or could not be PROVEN to resolve safely, so
  nothing about its contents is asserted.
- ``no_factory_dir`` — a real, browsable project whose ``.factory/`` directory is
  absent. Degraded but USABLE: plan, tickets and roadmap all read normally, and
  only run-state, runs and spend are legitimately missing (``.factory/`` is
  gitignored, so this is the ordinary state of a fresh clone).

Precedence is MOST-DEGRADED-FIRST: ``unreadable`` > ``path_missing`` >
``not_a_project`` > ``no_factory_dir`` > ``ok``. A resolver that observes more
than one of these reports the leftmost.

``unreadable`` must NEVER be folded into ``not_a_project``: "I could not look" is
not "I looked and there is nothing there". Collapsing them would report a
permission problem as an unregistered directory and send a human hunting for the
wrong fix — the same distinction
:data:`~factory_console.domain.runs.ArtifactSkipReason` and
:class:`~factory_console.domain.run_state.RunState` already draw.
"""


class RegisteredProject(BaseModel):
    """One durable console-DB row: a project the user asked this console to track.

    A row records the user's intent only, never the filesystem's answer — see the
    module docstring for why this is deliberately NOT
    :class:`~factory_console.domain.project.Project`. Frozen and
    ``extra='forbid'``, matching the rest of the domain surface.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: RegisteredProjectId
    """Stable identity of the row: a uuid4 hex matching
    :data:`REGISTERED_PROJECT_ID_PATTERN`. Minted by the store at registration and
    never reused, so it survives a rename or a re-point of ``path``."""

    name: str = Field(min_length=1)
    """Human label for the project, as the user gave it. Never empty — an unnamed
    row is unaddressable in a project switcher — and NOT derived from ``path``,
    which may change under it."""

    path: Path
    """The project root, ALWAYS in the canonical absolute form the store wrote
    (see ``store/paths.py``, T106). A consumer must NOT re-resolve it:
    re-resolving in a different working directory, or through a symlink that has
    since changed, would silently address a different project than the row the
    user registered."""

    addedAt: datetime
    """When the row was created — timezone-aware UTC. The instant the user's
    intent starts; it says nothing about the path's current state."""


class RegistryEntry(BaseModel):
    """A registry row joined with what the console currently knows about its path.

    This is the read-time projection the REST layer returns, and the shape the SPA
    renders. A :class:`RegisteredProject` alone is never enough for a UI: a row
    says nothing about whether its path still resolves, so a list built from rows
    alone would present a deleted or unreadable project as healthy. Frozen and
    ``extra='forbid'``.

    The wire field is named ``condition`` everywhere — never ``availability`` —
    because the union carries degraded-but-usable states, not a boolean about
    whether the project is available.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    project: RegisteredProject
    """The durable row, exactly as stored."""

    condition: RegistryEntryCondition
    """What the console observed about ``project.path`` at read time, resolved
    most-degraded-first per :data:`RegistryEntryCondition`."""
