"""The ``GET /api/v1/health`` liveness probe, honest about having no selection.

Relocated out of ``app.py`` (where the walking skeleton served it inline) into its
own v1 sub-router, mirroring ``api/v1/project.py``/``api/v1/tickets.py``: the
package ``__init__`` owns the ``/api/v1`` prefix, this module only names the route,
its OpenAPI tag, and the typed response envelope.

**This is the one project-scoped route that must never 409.** It is what an operator
— and the SPA's boot sequence — hits to find out WHY nothing else answers, so it
cannot itself be one of the things that stops answering. That is why it does NOT use
``Depends(get_current_project_root)`` in the argument list the way its siblings do:
that dependency RAISES, and a raise here would turn "no project is selected" into an
outage the boot probe reports as a broken server.

It resolves through the very same seam all the same — :func:`_resolve_selection`
CALLS :func:`~factory_console.api.deps.get_current_project_root` and catches its four
named failures — so the precedence rule stays declared exactly once, in
:mod:`factory_console.services.project_selection`. What changes is only the POLICY at
this edge: each failure becomes a reported condition instead of a status code.

``projectRoot`` is therefore nullable, and a named ``selectionReason`` drawn from
T111's :data:`~factory_console.services.project_selection.SelectionFailure` says which
condition holds. **That is a breaking narrowing** for any consumer that assumed a
string (the frontend's generated client, T121; the e2e harness, T120). It is what
honest-missing requires: a console with no selection reports a named absence rather
than a fabricated root. ``ok`` stays ``True`` in every case — the process is healthy
even when nothing is selected, and conflating the two would make the boot probe report
a misconfiguration as an outage.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import NamedTuple

import anyio.to_thread
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import get_current_project_root, get_selection_state
from factory_console.services.project_selection import (
    NoProjectSelected,
    RegistryUnreadable,
    SelectedProjectNotRegistered,
    SelectedProjectUnavailable,
    SelectionFailure,
    SelectionState,
)

_LOGGER = logging.getLogger(__name__)

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/project.py``).
router = APIRouter(tags=["health"])


class _ResolvedSelection(NamedTuple):
    """What the probe could establish about the current selection, never raising.

    The internal result type of :func:`_resolve_selection`: the three selection
    fields of :class:`HealthResponse`, resolved together because they are answered by
    one pass over the seam and are only ever consumed together.
    """

    project_root: Path | None
    selected_project_id: str | None
    selection_reason: SelectionFailure | None


class HealthResponse(BaseModel):
    """Liveness probe body: ``ok``, ``version``, and what the console is looking at.

    The last three fields are one answer in three parts, and each combination is a
    distinct, named state rather than an accident of nulls:

    * selection resolved → ``projectRoot`` set, ``selectedProjectId`` set,
      ``selectionReason`` ``None``.
    * nothing selected → all of ``projectRoot`` and ``selectedProjectId`` ``None``,
      ``selectionReason`` ``"no_selection"``.
    * selected but unusable → ``selectedProjectId`` set and ``selectionReason`` naming
      which failure; ``projectRoot`` is the unusable PATH when one is known (the row
      exists, the directory does not), and ``None`` when it is not (the id names no
      row at all).
    * the registry could not be read → every one of the three is ``None``. See
      :func:`_resolve_selection` for why that is not a ``selectionReason``.

    ``ok`` is ``True`` in all four: it reports the PROCESS, and none of these is a
    process fault.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    version: str
    projectRoot: Path | None
    selectedProjectId: str | None
    selectionReason: SelectionFailure | None


async def _read_selected_id(selection: SelectionState) -> str | None:
    """Return the currently selected id, RAISING when the store cannot be read.

    ``current_id`` reads through to the persisted selection when no session selection
    is set, so it is a blocking ``sqlite3`` call and is offloaded like every other —
    ``ARCHITECTURE.md``'s Cross-cutting **Concurrency** rule.

    The store failure is deliberately NOT swallowed here, even though this endpoint may
    not raise at its edge. Swallowing it to ``None`` made "no id is selected" and "I
    could not find out" the same value, and the caller then went on to resolve the
    selection a second time: a read that failed here but succeeded there produced
    ``projectRoot`` set with both ``selectedProjectId`` and ``selectionReason`` ``None``
    — a combination :class:`HealthResponse` documents as impossible, and the very shape
    that is supposed to mean "the console cannot say what is selected". Both reads have
    to reach the SAME conclusion about an unreadable store, so the policy decision
    belongs to :func:`_resolve_selection`, which owns the whole never-raise contract;
    this helper just reports what the store did.

    Raises:
        OSError: the console's own state directory could not be read.
        sqlite3.Error: the console's own database could not be read.
    """
    return await anyio.to_thread.run_sync(selection.current_id)


async def _resolve_selection(request: Request) -> _ResolvedSelection:
    """Resolve the current selection, or EXPLAIN why it could not be — never raising.

    The resolve-or-explain twin of
    :func:`~factory_console.api.deps.get_current_project_root`, built by calling it
    rather than by re-deriving the precedence beside it: a second copy of "pin, then
    session, then registry row, then path probe" would be a rule with one owner and
    two homes, and the two would drift the first time either moved. All of the
    blocking work — the registry reads and the path stat — happens inside that
    dependency, already offloaded; the extra :func:`_read_selected_id` call is the one
    this function adds, because the id is reported even in the failure cases the
    dependency raises without naming it.

    ``RegistryUnreadable`` is the one condition NOT turned into a ``selectionReason``,
    and deliberately: that union names states of the user's SELECTION, and "the
    console could not read its own store" is not one of them — see
    :class:`~factory_console.services.project_selection.RegistryUnreadable`, which is
    excluded from it for exactly that reason. Inventing a member for it here would be
    the second spelling the union exists to prevent. It is reported instead as all
    three fields ``None``, which is a state no other branch produces (every other
    absence carries a reason) and so stays distinguishable: "the console cannot
    currently say what is selected". ``ok`` remains ``True`` — an unreadable registry
    is what ``GET /api/v1/projects`` answers ``503`` about, and the probe's job is to
    report the condition, not to become a second outage signal for it.

    That all-``None`` answer is reached from BOTH store reads, and it has to be:
    :func:`_read_selected_id` and the dependency each read the selection, so either can
    be the one that finds the store unreadable, and the two must not disagree about what
    that means. They report it differently — the dependency has already wrapped it as
    :class:`RegistryUnreadable`, the bare helper has not — so both spellings are caught,
    landing every unreadable store on the one documented shape instead of on a mix of
    fields that contradicts the response model.

    Exactly ONE log line comes out of a failed probe, which is why the two spellings are
    caught separately rather than as one tuple: a :class:`RegistryUnreadable` was already
    logged WITH its cause by :func:`~factory_console.api.deps._read_registry`, so logging
    it again here would say the same thing twice at two levels, while the bare
    ``OSError`` / ``sqlite3.Error`` from the helper has been logged by nobody and would
    otherwise vanish — this endpoint reports it as "unknown" rather than raising it.
    """
    selection = get_selection_state(request)
    try:
        selected_id = await _read_selected_id(selection)
        root = await get_current_project_root(request)
    except NoProjectSelected as failure:
        return _ResolvedSelection(None, None, failure.reason)
    except SelectedProjectNotRegistered as failure:
        return _ResolvedSelection(None, failure.project_id, failure.reason)
    except SelectedProjectUnavailable as failure:
        return _ResolvedSelection(failure.path, selected_id, failure.reason)
    except RegistryUnreadable:
        # Already logged with its cause by ``deps._read_registry``.
        return _ResolvedSelection(None, None, None)
    except (OSError, sqlite3.Error) as error:
        _LOGGER.warning("health probe could not read the selected project id", exc_info=error)
        return _ResolvedSelection(None, None, None)
    return _ResolvedSelection(root, selected_id, None)


@router.get("/health")
async def get_health(request: Request) -> HealthResponse:
    """Return the liveness probe plus what the console currently has selected.

    ``version`` is read from ``app.state``, where ``create_app`` stashed it at boot.
    The three selection fields come from :func:`_resolve_selection`, which resolves
    through the same seam every other endpoint uses but reports its failures instead
    of raising them — so this route answers ``200`` whether a project is selected, is
    selected and gone, or was never selected at all. It is the only project-scoped
    route with no 409 and no 503, by design; see the module docstring.
    """
    resolved = await _resolve_selection(request)
    return HealthResponse(
        ok=True,
        version=request.app.state.version,
        projectRoot=resolved.project_root,
        selectedProjectId=resolved.selected_project_id,
        selectionReason=resolved.selection_reason,
    )
