"""The v2 ``POST`` / ``PUT`` / ``DELETE`` ``/api/v1/tickets`` write endpoints.

The three write verbs the SPA's edit form calls, and the ONLY routes in the app that
require the per-session write token: the guard is attached once as a router-level
``dependencies=[Depends(require_write_token)]``, so every route defined here is gated
and no read route anywhere gains a header (see
:mod:`factory_console.api.write_token`).

Every verb returns the SAME :class:`~factory_console.domain.write.WriteResult`
envelope carrying the unified diff, so the SPA's diff-preview modal and its post-save
confirmation share one shape, and every verb honours ``?dryRun=true`` to preview the
change without writing. Request and response bodies ARE the canonical
:mod:`factory_console.domain.write` models — there is no parallel api-model layer — so
these ordinary typed FastAPI routes auto-publish into ``/api/v1/openapi.json`` and the
frontend regenerates its TS types with no extra backend work.

The handlers mirror :mod:`factory_console.api.v1.tickets`: resolve the project root for
THIS request through :func:`~factory_console.api.deps.get_current_project_root`, load
the :class:`~factory_console.domain.project.Project` through the
read adapter, construct a request-scoped
:class:`~factory_console.services.write_service.WriteService` over the two injected
ports, and delegate. All request *logic* lives below this layer, so the handlers are
wiring plus one audit line (:func:`_log_write`): the service owns the create-collision
guard and the existence check (both on the dry-run path too), and the run-state
mutability gates live one layer further down, inside the writer's
``edit_ticket``/``delete_ticket``
(:func:`~factory_console.file_adapter.write_gate.ensure_mutable` and its
delete-path sibling :func:`~factory_console.file_adapter.write_gate.ensure_deletable`,
which also permits ``absent``) — so it guards an apply, and a dry-run previews a
non-mutable ticket rather than refusing it. The project load and the write-service call
are both BLOCKING filesystem work, so both are awaited through
``anyio.to_thread.run_sync(partial(...))`` per ``ARCHITECTURE.md``'s Cross-cutting
**Concurrency** rule — the writer does real disk I/O, which is the last thing that
belongs on the event loop.

*That offload is why every handler holds* :func:`~factory_console.api.deps.get_write_lock`
*across its whole body.* The same **Concurrency** rule also promises a single writer ("the
write path is serialized by the same single worker"), and while the load and the write ran
inline the event loop delivered that for free — a second write could not begin until the
first returned. Off-loaded onto anyio's thread pool they genuinely overlap, and a ticket
write is a read-modify-write of ``tickets.json`` with no lock anywhere below this layer:
two concurrent creates would each render a manifest from the same pre-write bytes and
last-write-wins would silently drop one entry (whose ``.md`` file was written all the
same), or both would pass the duplicate-id guard and neither get its 409. So the lock
wraps the load AND the service call together — the critical section is the whole
read-modify-write, not either half — restoring one-writer-at-a-time without putting the
disk I/O back on the loop.

**Two orderings decide what these routes are, and both are settled here.**

*The write token is checked BEFORE the selection.* ``require_write_token`` is attached at
the ROUTER, and FastAPI solves a route's router-level dependencies ahead of the ones
declared in the handler's own signature — so an unauthenticated caller is answered ``401
write_token_invalid`` without the selection ever being resolved. That is deliberate: the
401 stays as opaque as T64 made it, and a caller who cannot prove they may write learns
nothing about which project is selected, whether it is registered, or whether its path
still exists. No explicit sequencing code implements this; keeping the guard on the
router is what implements it, which is why it must not be moved into the handlers.

*An unresolvable selection REFUSES, and never falls back.* MONOTONICITY binds resolution
exactly as hard as it binds the run-state write gates: no selection, an unregistered id,
a vanished or unreadable path, or an unreadable registry each raise out of
:func:`~factory_console.api.deps.get_current_project_root` as the named 409
(``no_project_selected`` / ``selected_project_unavailable``). The pinned root is NOT
substituted. "I could not establish which project this is" must never be answered more
permissively than "I know exactly which project this is" — and on THESE routes the
permissive answer writes a ticket file, rewrites a manifest, or deletes a ticket in the
wrong repository. A silent write into the wrong project is the worst failure available in
this milestone and is unfalsifiable from the UI, whereas a named 409 is a thing the
operator can see and fix.

They also do no error handling of their own; every failure mode already has a
registered handler that renders the REST v1 envelope:
:class:`~factory_console.api.write_token.WriteTokenInvalid` (401),
``invalid_ticket_id`` (400, re-mapped from the pattern violation — the ``Path``
parameter on edit/delete, the ``TicketDraft.id`` body field on create),
``unknown_query_param`` and ``repeated_query_param`` (400, both from
:func:`reject_unknown_query_params`),
:class:`~factory_console.services.ticket_service.TicketNotFound` (404),
:class:`~factory_console.file_adapter.write_gate.TicketNotMutable` and
:class:`~factory_console.services.write_service.WriteConflict` (409),
:class:`~factory_console.services.write_service.WriteValidationError` (422), and the
selection seam's own :class:`~factory_console.services.project_selection.SelectionFailure`
members (409, plus 503 for ``RegistryUnreadable``). A ``try``/``except`` in a handler here
would only duplicate them.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, Query, Request, Response, status

from factory_console.api.deps import (
    TicketIdPath,
    get_current_project_root,
    get_file_adapter,
    get_file_writer,
    get_write_lock,
)
from factory_console.api.write_token import WRITE_TOKEN_SECURITY, require_write_token
from factory_console.domain.write import TicketDraft, TicketEdit, WriteResult
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.writer_protocol import FileWriter
from factory_console.logging import write_log_line
from factory_console.services.write_service import WriteService

# Every query key these routes understand. ``dryRun`` is the only one; the guard below
# rejects anything else rather than letting it pass unread.
_ALLOWED_QUERY_KEYS = frozenset({"dryRun"})

_LOGGER = logging.getLogger(__name__)


def _log_write(verb: str, result: WriteResult) -> None:
    """Record one audit line for a completed write, naming the files it touched.

    The access log is not enough on its own to reconstruct what a write did: it
    formats ``request.url.path``, which drops the query string, and edit/delete answer
    ``200`` whether they applied or previewed — so ``DELETE /api/v1/tickets/T64 200``
    is byte-identical for a preview that changed nothing and an apply that deleted
    ``<id>.md`` and rewrote ``tickets.json`` and ``ROADMAP.md``. This line carries the
    one bit that separates them (``applied``) plus the paths actually written, so a bad
    write can be traced to the files it modified.

    The values are folded INTO the message by
    :func:`~factory_console.logging.write_log_line` rather than passed via ``extra=``,
    exactly as :class:`~factory_console.app.AccessLogMiddleware` does with
    :func:`~factory_console.logging.request_log_line`: the app installs a single
    message-only formatter, which renders no record attribute it does not name, so an
    ``extra=`` payload would reach the operator as a bare ``ticket write`` — the very
    gap this line exists to close. The write token is never among the values; the audit
    records what changed, not who proved they could.
    """
    _LOGGER.info(
        write_log_line(verb, result.ticketId, result.applied, result.changedFiles),
    )


def reject_unknown_query_params(request: Request) -> None:
    """Reject any unrecognized OR repeated query key on these write routes, as a 400.

    Two ways the one flag that separates "show me the diff" from "rewrite the manifest,
    the ticket file, and the roadmap" can fail OPEN, and this guard closes both:

    * an **unrecognized** key — Starlette hands undeclared keys to nobody, so a
      plausible miscasing like ``?dryrun=true`` or ``?dry_run=true`` would leave
      ``dry_run`` at its ``False`` default and take the APPLY branch, and
    * a **repeated** key — ``?dryRun=true&dryRun=false`` carries the allowed name, but
      FastAPI binds a scalar ``bool`` through last-wins ``QueryParams.get``, so the
      request that explicitly asked for a preview APPLIES. Checking the key *set* alone
      cannot see this: the duplicate collapses before the allow-list is consulted, so
      the multi-item list is what has to be inspected. Reachable with no malice at all —
      any caller or proxy that appends ``&dryRun=true`` to a URL already carrying a
      ``dryRun`` value gets a write while asking for a preview.

    On routes whose apply path deletes a file, the safety flag must fail CLOSED: an
    unusable query string is an error, never a silent apply.

    Attached at the router so it covers all three verbs and cannot be forgotten on a
    fourth. Read routes are unaffected — this lives only on this module's router.

    Raises:
        FactoryConsoleError: A query key is unrecognized (``unknown_query_param``) or
            given more than once (``repeated_query_param``); both 400.
    """
    params = request.query_params
    unknown = sorted({key for key, _ in params.multi_items()} - _ALLOWED_QUERY_KEYS)
    if unknown:
        raise FactoryConsoleError(
            code="unknown_query_param",
            message=f"Unrecognized query parameter(s): {', '.join(unknown)}",
            status=400,
            details={"unknown": unknown, "allowed": sorted(_ALLOWED_QUERY_KEYS)},
        )

    # Only allowed keys remain, so any duplicate here is a repeated `dryRun`.
    repeated = sorted({key for key, _ in params.multi_items() if len(params.getlist(key)) > 1})
    if repeated:
        raise FactoryConsoleError(
            code="repeated_query_param",
            message=(
                f"Query parameter(s) given more than once: {', '.join(repeated)}. "
                "Send each at most once."
            ),
            status=400,
            details={"repeated": repeated},
        )


# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names the
# routes and their OpenAPI tag (mirrors ``api/v1/tickets.py``) — plus the write-token
# dependency, which is what makes this module, and nothing else, header-gated, and the
# strict query guard that keeps ``?dryRun`` from failing open on a typo.
router = APIRouter(
    tags=["tickets"],
    dependencies=[Depends(require_write_token), Depends(reject_unknown_query_params)],
)

# The dry-run flag, shared by all three verbs. The wire name is camelCase per the REST
# v1 contract; the alias keeps the Python parameter snake_case so it feeds ``WriteService``'s
# ``dry_run`` keyword without renaming the concept mid-call.
_DryRunFlag = Annotated[bool, Query(alias="dryRun")]

# A dry-run answers 200 (nothing was created) while an apply answers 201, so the
# create operation publishes BOTH outcomes against the one ``WriteResult`` shape.
_DRY_RUN_RESPONSE: dict[int | str, dict[str, Any]] = {
    status.HTTP_200_OK: {
        "model": WriteResult,
        "description": "Dry-run preview: the diff that WOULD be written; nothing was created.",
    }
}


@router.post(
    "/tickets",
    status_code=status.HTTP_201_CREATED,
    responses=_DRY_RUN_RESPONSE,
    openapi_extra=WRITE_TOKEN_SECURITY,
)
async def create_ticket(
    payload: TicketDraft,
    response: Response,
    dry_run: _DryRunFlag = False,
    adapter: FileAdapter = Depends(get_file_adapter),
    writer: FileWriter = Depends(get_file_writer),
    root: Path = Depends(get_current_project_root),
    write_lock: asyncio.Lock = Depends(get_write_lock),
) -> WriteResult:
    """Create the ticket described by ``payload``, or preview it when ``?dryRun=true``.

    The status code follows the RESULT rather than the query flag: an apply reports
    ``201 Created``, a dry-run ``200 OK``, because a preview creates nothing and a
    ``201`` would tell the SPA (and any cache between them) that it did. Setting
    ``response.status_code`` overrides the route's declared default, which stays ``201``
    so that remains the documented success code.

    Writes into the SELECTED project at the per-request ``root``; an unresolvable
    selection refuses with the named 409 before any port is touched. Delegates to
    :meth:`~factory_console.services.write_service.WriteService.create`, whose
    ``WriteConflict`` (409) propagates for an id that already exists. Both blocking calls
    are awaited off the event loop, under ``write_lock`` for their combined duration —
    they are one read-modify-write of the manifest, and that guard is what makes the
    duplicate-id ``WriteConflict`` above hold against a concurrent create of the same id.
    """
    async with write_lock:
        project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
        result = await anyio.to_thread.run_sync(
            partial(WriteService(writer, adapter).create, project, payload, dry_run=dry_run)
        )
    _log_write("create", result)
    response.status_code = status.HTTP_201_CREATED if result.applied else status.HTTP_200_OK
    return result


@router.put("/tickets/{ticket_id}", openapi_extra=WRITE_TOKEN_SECURITY)
async def edit_ticket(
    ticket_id: TicketIdPath,
    payload: TicketEdit,
    dry_run: _DryRunFlag = False,
    adapter: FileAdapter = Depends(get_file_adapter),
    writer: FileWriter = Depends(get_file_writer),
    root: Path = Depends(get_current_project_root),
    write_lock: asyncio.Lock = Depends(get_write_lock),
) -> WriteResult:
    """Apply ``payload`` to ``ticket_id``, or preview the edit when ``?dryRun=true``.

    Always ``200``: an edit creates no resource on either path. Edits the ticket in the
    SELECTED project at the per-request ``root``; an unresolvable selection refuses with
    the named 409 before any port is touched. Delegates to
    :meth:`~factory_console.services.write_service.WriteService.edit`, whose
    ``TicketNotFound`` (404) and ``TicketNotMutable`` (409, for a run-state outside the
    EDIT allowlist ``todo``/``unknown``) propagate to the registered handlers. Both
    blocking calls are awaited off the event loop, under ``write_lock`` for their combined
    duration — an edit rewrites the manifest from what the load observed, so it is the same
    read-modify-write the module docstring's create case describes.
    """
    async with write_lock:
        project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
        result = await anyio.to_thread.run_sync(
            partial(
                WriteService(writer, adapter).edit,
                project,
                ticket_id,
                payload,
                dry_run=dry_run,
            )
        )
    _log_write("edit", result)
    return result


@router.delete("/tickets/{ticket_id}", openapi_extra=WRITE_TOKEN_SECURITY)
async def delete_ticket(
    ticket_id: TicketIdPath,
    dry_run: _DryRunFlag = False,
    adapter: FileAdapter = Depends(get_file_adapter),
    writer: FileWriter = Depends(get_file_writer),
    root: Path = Depends(get_current_project_root),
    write_lock: asyncio.Lock = Depends(get_write_lock),
) -> WriteResult:
    """Delete ``ticket_id``, or preview the delete when ``?dryRun=true``.

    Always ``200`` with the uniform :class:`WriteResult` body rather than a bodiless
    ``204``, because the SPA renders the delete's diff in the same confirmation view as
    a create or an edit. Deletes from the SELECTED project at the per-request ``root``;
    an unresolvable selection refuses with the named 409 before any port is touched —
    the request whose fail-open would destroy a file in the wrong repository. Delegates
    to :meth:`~factory_console.services.write_service.WriteService.delete`, whose
    ``TicketNotFound`` (404) and ``TicketNotMutable`` (409) propagate to the registered
    handlers. Both blocking calls are awaited off the event loop, under ``write_lock`` for
    their combined duration — a delete rewrites the manifest from what the load observed,
    so it is the same read-modify-write the module docstring's create case describes.
    """
    async with write_lock:
        project = await anyio.to_thread.run_sync(partial(adapter.load_project, root))
        result = await anyio.to_thread.run_sync(
            partial(WriteService(writer, adapter).delete, project, ticket_id, dry_run=dry_run)
        )
    _log_write("delete", result)
    return result
