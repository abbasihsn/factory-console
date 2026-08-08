"""FastAPI exception handlers mapping domain errors to the REST v1 envelope.

Keeps the framework-free :mod:`factory_console.errors` decoupled from FastAPI:
this module is the ONLY place that knows both ``FactoryConsoleError`` and
``JSONResponse``. :func:`register_error_handlers` installs two handlers on the
app:

* every :class:`~factory_console.errors.FactoryConsoleError` subtype (present and
  future — ``ProjectNotFound``, ``MalformedManifest``, ``PathTraversal``,
  ``TicketFileMissing``, …) is rendered via
  :func:`~factory_console.errors.to_error_response` at the status the exception
  declares, so ONE handler covers them all transparently, and
* a :class:`~fastapi.exceptions.RequestValidationError` becomes a
  ``validation_error`` (422) envelope, EXCEPT when it is a ticket-id pattern
  violation, which is re-mapped to the exact ``invalid_ticket_id`` (400)
  envelope a deep :class:`~factory_console.file_adapter.path_safety.PathTraversal`
  would produce — so an invalid ticket id yields ONE envelope for the SPA wherever
  it is rejected: the FastAPI ``Path`` boundary (``GET``/``PUT``/``DELETE``), the
  ``TicketDraft.id`` body field a create carries instead of a path param, or
  deeper in ``_safe_resolve``.
"""

from __future__ import annotations

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from factory_console.errors import FactoryConsoleError, to_error_response
from factory_console.file_adapter.path_safety import PathTraversal


def _is_ticket_id_loc(loc: object) -> bool:
    """Return whether ``loc`` addresses a ticket id the SPA can mistype.

    Two shapes carry one, and both must map to the same envelope:

    * ``('path', …, 'ticket_id')`` — the FastAPI ``Path`` parameter on
      ``GET``/``PUT``/``DELETE /tickets/{ticket_id}``, and
    * ``('body', 'id')`` — the :class:`~factory_console.domain.write.TicketDraft`
      field a ``POST /tickets`` carries INSTEAD of a path param, whose ``TicketId``
      annotation enforces the very same :data:`TICKET_ID_PATTERN`.

    Without the second, one user mistake would answer ``400 invalid_ticket_id`` on
    edit/delete but ``422 validation_error`` on create, and the SPA could not branch
    on ``error.code`` alone.
    """
    if not isinstance(loc, tuple | list) or not loc:
        return False
    if loc[0] == "path" and loc[-1] == "ticket_id":
        return True
    return tuple(loc) == ("body", "id")


def _ticket_id_pattern_violation(
    errors: list[dict[str, object]],
) -> dict[str, object] | None:
    """Return the first ticket-id pattern-mismatch entry, or ``None``.

    A match is an error entry whose ``type`` is ``'string_pattern_mismatch'`` and
    whose ``loc`` :func:`passes <_is_ticket_id_loc>` — i.e. a ticket id failing
    :data:`TICKET_ID_PATTERN` at either boundary that can carry one. Returns
    ``None`` when no entry matches so the caller falls through to the generic
    ``validation_error`` envelope. A single request may carry several error
    entries; the first ticket-id violation short-circuits the whole response.
    """
    for err in errors:
        if err.get("type") == "string_pattern_mismatch" and _is_ticket_id_loc(err.get("loc")):
            return err
    return None


_LOGGER = logging.getLogger(__name__)
"""Where a mapped 5xx leaves its trace — application log, not the access log."""


def register_error_handlers(app: FastAPI) -> None:
    """Register the domain-error and validation-error handlers on ``app``."""

    @app.exception_handler(FactoryConsoleError)
    async def _handle_domain_error(request: Request, exc: FactoryConsoleError) -> JSONResponse:
        """Render any :class:`FactoryConsoleError` subtype to its declared envelope.

        A 5xx is LOGGED with its traceback before it is rendered. Being *mapped* is
        exactly why it would otherwise be silent: an unhandled exception reaches
        Starlette's ``ServerErrorMiddleware`` and gets a traceback, while a mapped
        one is answered here and never propagates — so a genuine data failure
        (``RoadmapUnreadable``, ``TicketFileUnreadable``, a manifest that went
        malformed after boot) left the operator nothing but the access line's bare
        ``500``, with no cause and no path to chase. 4xx stays unlogged: it is the
        client's error, the access line already records it, and logging it would let
        a caller fill the log by looping on a bad id.
        """
        if exc.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            # The code is in the MESSAGE, not only in `extra`: `configure_logging`'s
            # formatter renders the message alone, so a field-only code prints nowhere.
            _LOGGER.error(
                "domain error %s (%d) on %s %s",
                exc.code,
                exc.status,
                request.method,
                request.url.path,
                exc_info=exc,
                extra={"code": exc.code, "status": exc.status, "path": request.url.path},
            )
        return JSONResponse(status_code=exc.status, content=to_error_response(exc))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Map request validation failures to the ``validation_error`` envelope.

        Special-cases a ticket-id pattern violation — at the ``Path`` boundary or in
        a create's ``id`` body field — to the identical ``invalid_ticket_id`` (400)
        envelope a deep ``PathTraversal`` yields, so an invalid ticket id is rejected
        uniformly regardless of verb or validation depth.
        ``details`` runs through :func:`jsonable_encoder` because raw pydantic
        errors can carry values (exceptions, ``bytes``) that are not JSON-native.
        """
        errors = exc.errors()
        offending = _ticket_id_pattern_violation(errors)
        if offending is not None:
            traversal = PathTraversal.from_pattern_violation(str(offending.get("input")))
            return JSONResponse(
                status_code=traversal.status,
                content=to_error_response(traversal),
            )
        validation_error = FactoryConsoleError(
            code="validation_error",
            message="Request validation failed",
            status=422,
            details=jsonable_encoder(errors),
        )
        return JSONResponse(
            status_code=validation_error.status,
            content=to_error_response(validation_error),
        )
