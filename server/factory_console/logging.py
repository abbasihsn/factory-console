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

# The level names the console accepts, in descending severity. Python's ``logging``
# only recognizes these uppercase names, so a level supplied on the CLI must be
# normalized to one of them before use (see :func:`normalize_log_level`).
LOG_LEVELS: tuple[str, ...] = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def normalize_log_level(level: str) -> str | None:
    """Return the canonical uppercase level name, or ``None`` if it is unknown.

    Accepts any case (``'debug'`` -> ``'DEBUG'``) so the natural lowercase form
    works, and returns ``None`` for a name outside :data:`LOG_LEVELS` so the caller
    can reject it cleanly instead of crashing inside ``logging``'s ``setLevel``.
    """
    normalized = level.upper()
    return normalized if normalized in LOG_LEVELS else None


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
