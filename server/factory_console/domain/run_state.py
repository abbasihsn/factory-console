"""The lifecycle state of a ticket in the factory run-state directory.

Design decision — the string VALUES mirror the on-disk run-state directory names
under ``.factory/run-state/`` (see ``ARCHITECTURE.md`` "Factory run-state
directory"): ``todo``, ``in-flight`` (hyphenated), ``ready``, ``merged`` — plus
``unknown`` for the "no run-state directory present" case. Keeping the value
equal to the directory name gives one obvious convention: the value only affects
JSON serialization, and the file-adapter still maps a directory to its enum
*member* (``in-flight`` dir -> :attr:`RunState.in_flight`), never by string
guessing. These values are pinned by a test so they cannot silently drift.
"""

from __future__ import annotations

from enum import Enum


# The ``(str, Enum)`` mixin form is fixed by the T07 spec (the pinned test locks
# it as a ``str`` subclass). ``enum.StrEnum`` (3.11+) is the ruff-preferred
# alternative but changes ``str(member)``; we keep the spec'd form deliberately.
class RunState(str, Enum):  # noqa: UP042
    """A ticket's run-state, derived by probing the factory run-state directory.

    Subclasses ``str`` so a member compares and serializes as its string value
    (``RunState.todo == "todo"``); the value is the on-disk directory name.
    """

    todo = "todo"
    in_flight = "in-flight"
    ready = "ready"
    merged = "merged"
    unknown = "unknown"
