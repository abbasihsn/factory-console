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
  ``validation_error`` (422) envelope, EXCEPT when it is a ``ticket_id`` ``Path``
  pattern violation, which is re-mapped to the exact ``invalid_ticket_id`` (400)
  envelope a deep :class:`~factory_console.file_adapter.path_safety.PathTraversal`
  would produce — so an invalid ticket id yields ONE envelope for the SPA whether
  it is rejected at the FastAPI ``Path`` boundary or deeper in ``_safe_resolve``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from factory_console.domain import TICKET_ID_PATTERN
from factory_console.errors import FactoryConsoleError, to_error_response
from factory_console.file_adapter.path_safety import PathTraversal


def _ticket_id_pattern_violation(
    errors: list[dict[str, object]],
) -> dict[str, object] | None:
    """Return the first ``ticket_id`` ``Path`` pattern-mismatch entry, or ``None``.

    A match is an error entry whose ``type`` is ``'string_pattern_mismatch'`` and
    whose ``loc`` is a ``('path', …, 'ticket_id')`` tuple — the FastAPI ``Path``
    parameter named ``ticket_id`` failing :data:`TICKET_ID_PATTERN`. Returns
    ``None`` when no entry matches so the caller falls through to the generic
    ``validation_error`` envelope. A single request may carry several error
    entries; the first ticket-id violation short-circuits the whole response.
    """
    for err in errors:
        loc = err.get("loc")
        if (
            err.get("type") == "string_pattern_mismatch"
            and isinstance(loc, tuple | list)
            and loc
            and loc[0] == "path"
            and loc[-1] == "ticket_id"
        ):
            return err
    return None


def register_error_handlers(app: FastAPI) -> None:
    """Register the domain-error and validation-error handlers on ``app``."""

    @app.exception_handler(FactoryConsoleError)
    async def _handle_domain_error(_request: Request, exc: FactoryConsoleError) -> JSONResponse:
        """Render any :class:`FactoryConsoleError` subtype to its declared envelope."""
        return JSONResponse(status_code=exc.status, content=to_error_response(exc))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Map request validation failures to the ``validation_error`` envelope.

        Special-cases a ``ticket_id`` ``Path`` pattern violation to the identical
        ``invalid_ticket_id`` (400) envelope a deep ``PathTraversal`` yields, so an
        invalid ticket id is rejected uniformly regardless of validation depth.
        ``details`` runs through :func:`jsonable_encoder` because raw pydantic
        errors can carry values (exceptions, ``bytes``) that are not JSON-native.
        """
        errors = exc.errors()
        offending = _ticket_id_pattern_violation(errors)
        if offending is not None:
            traversal = PathTraversal(
                str(offending.get("input")),
                reason=f"Ticket id must match {TICKET_ID_PATTERN}",
            )
            return JSONResponse(
                status_code=traversal.status,
                content=to_error_response(traversal),
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": jsonable_encoder(errors),
                }
            },
        )
