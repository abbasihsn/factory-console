"""Shared error base and the REST v1 error-envelope helper.

Concrete subclasses live in the modules that raise them (``file_adapter/*``,
``services/*``), keeping the exception owner and the raiser co-located.
"""


class FactoryConsoleError(Exception):
    """Base for Factory Console domain errors, carrying a transport-ready payload.

    Attributes:
        code: Stable machine-readable error code (the envelope's ``error.code``).
        message: Human-readable description (the envelope's ``error.message``).
        status: HTTP status the edge layer maps this error to.
        details: Optional structured context; omitted from the envelope when ``None``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def to_error_response(exc: FactoryConsoleError) -> dict[str, object]:
    """Build the REST v1 error envelope ``{'error': {'code', 'message', 'details'?}}``.

    ``details`` is included only when ``exc.details is not None`` — a falsy-but-not-None
    value (``0``, ``''``, ``[]``) is still emitted; ``None`` drops the key entirely.
    """
    error: dict[str, object] = {"code": exc.code, "message": exc.message}
    if exc.details is not None:
        error["details"] = exc.details
    return {"error": error}
