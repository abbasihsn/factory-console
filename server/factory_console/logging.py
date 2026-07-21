"""Root-logger configuration and the shared request-log-line formatter.

Cross-cutting observability substrate imported by the backend entrypoint and the
file-adapter/service layers. ``configure_logging`` owns the process-wide handler
setup and is idempotent-safe, so repeat calls never stack duplicate handlers;
``request_log_line`` is a pure formatter that RETURNS a greppable one-line string
and never emits it, leaving the caller to decide when and where to log.
"""

import logging
import sys

# Root formatter for every emitted record: level, ISO-ish timestamp, then message.
_LOG_FORMAT = "%(levelname)s %(asctime)s %(message)s"


def configure_logging(level: str) -> None:
    """Point the root logger at ``stderr`` with ``_LOG_FORMAT`` at ``level``.

    ``level`` is a level *name* such as ``'INFO'`` or ``'DEBUG'``. Existing root
    handlers are removed before the new one is attached, so calling this more than
    once replaces rather than accumulates handlers (idempotent-safe).
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)


def request_log_line(method: str, path: str, status: int, dur_ms: float) -> str:
    """Return a single greppable request-log line; this does not emit it.

    Example: ``request_log_line('GET', '/health', 200, 1.5)`` yields
    ``'GET /health 200 1.5ms'``.
    """
    return f"{method} {path} {status} {dur_ms:.1f}ms"
