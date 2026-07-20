"""The :class:`RunState` enum — a ticket's factory run-state.

Derived by probing the factory run-state directory (see ``ARCHITECTURE.md``).
Values are stable string keys and are pinned by tests; do not change them.
"""

from enum import StrEnum


class RunState(StrEnum):
    """Factory run-state of a ticket.

    ``StrEnum`` (a ``str`` subclass) so members serialize as their plain string
    value in JSON and ``model_dump()`` output.
    """

    todo = "todo"
    in_flight = "in_flight"
    ready = "ready"
    merged = "merged"
    unknown = "unknown"
