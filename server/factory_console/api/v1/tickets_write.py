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
ports, and delegate. All request logic — the create-collision guard, the existence
check, and the todo-only mutability gate — lives in that service, so the handlers are
wiring only.

They also do no error handling of their own; every failure mode already has a
registered handler that renders the REST v1 envelope:
:class:`~factory_console.api.write_token.WriteTokenInvalid` (401),
``invalid_ticket_id`` (400, re-mapped from the ``Path`` pattern violation),
:class:`~factory_console.services.ticket_service.TicketNotFound` (404),
:class:`~factory_console.file_adapter.write_gate.TicketNotMutable` and
:class:`~factory_console.services.write_service.WriteConflict` (409), and
:class:`~factory_console.services.write_service.WriteValidationError` (422). A
``try``/``except`` in a handler here would only duplicate them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi import Path as PathParam

from factory_console.api.deps import get_file_adapter, get_file_writer
from factory_console.api.write_token import WRITE_TOKEN_SCHEME_NAME, require_write_token
from factory_console.domain.ticket import TICKET_ID_PATTERN
from factory_console.domain.write import TicketDraft, TicketEdit, WriteResult
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.file_adapter.writer_protocol import FileWriter
from factory_console.services.write_service import WriteService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names the
# routes and their OpenAPI tag (mirrors ``api/v1/tickets.py``) — plus the write-token
# dependency, which is what makes this module, and nothing else, header-gated.
router = APIRouter(tags=["tickets"], dependencies=[Depends(require_write_token)])

# ``require_write_token`` is a plain dependency rather than a ``SecurityBase``, so
# FastAPI cannot derive a ``security`` requirement from it. Each write operation
# therefore names the scheme ``publish_write_token_scheme`` publishes, or the OpenAPI
# document would describe a header that no operation actually requires.
_WRITE_TOKEN_SECURITY: dict[str, Any] = {"security": [{WRITE_TOKEN_SCHEME_NAME: []}]}

# The dry-run flag, shared by all three verbs. The wire name is camelCase per the REST
# v1 contract; the alias keeps the Python parameter snake_case so it feeds ``WriteService``'s
# ``dry_run`` keyword without renaming the concept mid-call.
_DryRunFlag = Annotated[bool, Query(alias="dryRun")]

# The ticket id in the path, validated against the shared pattern at the FastAPI
# boundary (mirrors ``api/v1/tickets.py``), so an invalid id becomes the
# ``invalid_ticket_id`` 400 envelope and never reaches the adapter or the writer.
_TicketIdPath = Annotated[str, PathParam(pattern=TICKET_ID_PATTERN)]

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
    response.status_code = status.HTTP_201_CREATED if result.applied else status.HTTP_200_OK
    return result


@router.put("/tickets/{ticket_id}", openapi_extra=_WRITE_TOKEN_SECURITY)
async def edit_ticket(
    ticket_id: _TicketIdPath,
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
    return WriteService(writer, adapter).edit(project, ticket_id, payload, dry_run=dry_run)


@router.delete("/tickets/{ticket_id}", openapi_extra=_WRITE_TOKEN_SECURITY)
async def delete_ticket(
    ticket_id: _TicketIdPath,
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
    return WriteService(writer, adapter).delete(project, ticket_id, dry_run=dry_run)
