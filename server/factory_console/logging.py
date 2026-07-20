"""Shared logging substrate: root-logger setup and the request log-line format.

Every backend endpoint and file-adapter module imports from here so diagnostics
share one formatter and one log-line shape.
"""

import logging
import sys

_LOG_FORMAT = "%(levelname)s %(asctime)s %(message)s"


def configure_logging(level: str) -> None:
    """Set the root logger ``level`` and attach a single stderr stream handler.

    Idempotent: repeated calls (many modules configure logging on import) clear
    any existing handlers first so the stderr handler is never stacked twice.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)


def request_log_line(method: str, path: str, status: int, dur_ms: float) -> str:
    """Return a formatted one-line summary of a handled HTTP request."""
    return f"{method} {path} {status} {dur_ms:.1f}ms"
