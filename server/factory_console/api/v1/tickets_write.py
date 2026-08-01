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

The handlers mirror :mod:`factory_console.api.v1.tickets`: resolve the project root off
``app.state``, load the :class:`~factory_console.domain.project.Project` through the
read adapter, construct a request-scoped
:class:`~factory_console.services.write_service.WriteService` over the two injected
ports, and delegate. All request *logic* lives below this layer, so the handlers are
wiring plus one audit line (:func:`_log_write`): the service owns the create-collision
guard and the existence check (both on the dry-run path too), and the todo-only
mutability gate lives one layer further down, inside the writer's
``edit_ticket``/``delete_ticket``
(:func:`~factory_console.file_adapter.write_gate.ensure_mutable` and its
delete-path sibling :func:`~factory_console.file_adapter.write_gate.ensure_deletable`,
which also permits ``absent``) — so it guards an apply, and a dry-run previews a
non-mutable ticket rather than refusing it.

They also do no error handling of their own; every failure mode already has a
registered handler that renders the REST v1 envelope:
:class:`~factory_console.api.write_token.WriteTokenInvalid` (401),
``invalid_ticket_id`` (400, re-mapped from the pattern violation — the ``Path``
parameter on edit/delete, the ``TicketDraft.id`` body field on create),
``unknown_query_param`` and ``repeated_query_param`` (400, both from
:func:`reject_unknown_query_params`),
:class:`~factory_console.services.ticket_service.TicketNotFound` (404),
:class:`~factory_console.file_adapter.write_gate.TicketNotMutable` and
:class:`~factory_console.services.write_service.WriteConflict` (409), and
:class:`~factory_console.services.write_service.WriteValidationError` (422). A
``try``/``except`` in a handler here would only duplicate them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status

from factory_console.api.deps import TicketIdPath, get_file_adapter, get_file_writer
from factory_console.api.write_token import WRITE_TOKEN_SCHEME_NAME, require_write_token
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

# ``require_write_token`` is a plain dependency rather than a ``SecurityBase``, so
# FastAPI cannot derive a ``security`` requirement from it. Each write operation
# therefore names the scheme ``publish_write_token_scheme`` publishes, or the OpenAPI
# document would describe a header that no operation actually requires.
_WRITE_TOKEN_SECURITY: dict[str, Any] = {"security": [{WRITE_TOKEN_SCHEME_NAME: []}]}

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
    openapi_extra=_WRITE_TOKEN_SECURITY,
)
async def create_ticket(
    payload: TicketDraft,
    request: Request,
    response: Response,
    dry_run: _DryRunFlag = False,
    adapter: FileAdapter = Depends(get_file_adapter),
    writer: FileWriter = Depends(get_file_writer),
) -> WriteResult:
    """Create the ticket described by ``payload``, or preview it when ``?dryRun=true``.

    The status code follows the RESULT rather than the query flag: an apply reports
    ``201 Created``, a dry-run ``200 OK``, because a preview creates nothing and a
    ``201`` would tell the SPA (and any cache between them) that it did. Setting
    ``response.status_code`` overrides the route's declared default, which stays ``201``
    so that remains the documented success code.

    Delegates to :meth:`~factory_console.services.write_service.WriteService.create`,
    whose ``WriteConflict`` (409) propagates for an id that already exists.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    result = WriteService(writer, adapter).create(project, payload, dry_run=dry_run)
    _log_write("create", result)
    response.status_code = status.HTTP_201_CREATED if result.applied else status.HTTP_200_OK
    return result


@router.put("/tickets/{ticket_id}", openapi_extra=_WRITE_TOKEN_SECURITY)
async def edit_ticket(
    ticket_id: TicketIdPath,
    payload: TicketEdit,
    request: Request,
    dry_run: _DryRunFlag = False,
    adapter: FileAdapter = Depends(get_file_adapter),
    writer: FileWriter = Depends(get_file_writer),
) -> WriteResult:
    """Apply ``payload`` to ``ticket_id``, or preview the edit when ``?dryRun=true``.

    Always ``200``: an edit creates no resource on either path. Delegates to
    :meth:`~factory_console.services.write_service.WriteService.edit`, whose
    ``TicketNotFound`` (404) and ``TicketNotMutable`` (409, the todo-only editing rule)
    propagate to the registered handlers.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    result = WriteService(writer, adapter).edit(project, ticket_id, payload, dry_run=dry_run)
    _log_write("edit", result)
    return result


@router.delete("/tickets/{ticket_id}", openapi_extra=_WRITE_TOKEN_SECURITY)
async def delete_ticket(
    ticket_id: TicketIdPath,
    request: Request,
    dry_run: _DryRunFlag = False,
    adapter: FileAdapter = Depends(get_file_adapter),
    writer: FileWriter = Depends(get_file_writer),
) -> WriteResult:
    """Delete ``ticket_id``, or preview the delete when ``?dryRun=true``.

    Always ``200`` with the uniform :class:`WriteResult` body rather than a bodiless
    ``204``, because the SPA renders the delete's diff in the same confirmation view as
    a create or an edit. Delegates to
    :meth:`~factory_console.services.write_service.WriteService.delete`, whose
    ``TicketNotFound`` (404) and ``TicketNotMutable`` (409) propagate to the registered
    handlers.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    result = WriteService(writer, adapter).delete(project, ticket_id, dry_run=dry_run)
    _log_write("delete", result)
    return result
