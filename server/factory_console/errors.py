"""Base application error and the REST v1 error-envelope helper.

Concrete subclasses live in the modules that raise them (``file_adapter/*``,
``services/*``), keeping the exception owner and the raiser co-located.
"""

from typing import Any


class FactoryConsoleError(Exception):
    """Base for application errors carrying an API error code, message, and HTTP status."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        details: object | None = None,
    ) -> None:
        """Store the envelope fields and initialise the base ``Exception`` with ``message``."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def to_error_response(exc: FactoryConsoleError) -> dict[str, Any]:
    """Render ``exc`` as the REST v1 error envelope, omitting ``details`` when it is ``None``."""
    return {
        "error": {
            "code": exc.code,
            "message": exc.message,
            **({"details": exc.details} if exc.details is not None else {}),
        }
    }
