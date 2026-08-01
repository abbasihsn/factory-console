"""The lifecycle state of a ticket, as named by whichever run-state source said so.

Design decision — a member's string VALUE mirrors the name the SOURCE that
contributed it uses, not one canonical spelling:

- The legacy run-state *directory* (``.factory/run-state/<state>/<id>``, see
  ``ARCHITECTURE.md`` "Factory run-state directory (read-only)") names ``todo``,
  ``in-flight`` (hyphenated), ``ready``, ``merged``.
- The factory's ``.factory/run-state.json`` names the nine ``FAC_STATES``:
  ``todo in_progress ready in_part in_submilestone merged flagged failed
  needs_human`` (underscored). There is no ``in-flight`` in the factory at all —
  it exists here only because the directory form names it.

``unknown`` belongs to neither source: it is the "no run-state source present or
resolvable" answer. ``absent`` is different again: a run-state source WAS
resolved, but it does not list the ticket being asked about — a ticket added to
``tickets.json`` by hand after the last factory run, or a mistyped id. The two
must never collapse into each other: ``unknown`` is "there is no source to ask";
``absent`` is "the source answered, and its answer is 'not listed'". Neither is
mutable in the write gate; ``unknown`` alone stays editable because a project
with no run-state source at all must remain fully usable in the console. Because
two vocabularies meet here, NO name is ever interpreted by string munging
(``in_progress`` and ``in-flight`` differ by exactly the kind of character a
``.replace()`` gets away with until it doesn't): the file-adapter's explicit
alias table
(:data:`~factory_console.file_adapter.run_state.FACTORY_STATUS_ALIASES`) is the
single place a source's name becomes a member. These values are pinned by a test
so they cannot silently drift.
"""

from __future__ import annotations

from enum import Enum


# The ``(str, Enum)`` mixin form is fixed by the T07 spec (the pinned test locks
# it as a ``str`` subclass). ``enum.StrEnum`` (3.11+) is the ruff-preferred
# alternative but changes ``str(member)``; we keep the spec'd form deliberately.
class RunState(str, Enum):  # noqa: UP042
    """A ticket's run-state, derived from the project's resolved run-state source.

    Subclasses ``str`` so a member compares and serializes as its string value
    (``RunState.todo == "todo"``); the value is the name its source uses.
    """

    # Named by the legacy run-state DIRECTORY form (and, for the three shared
    # names, by the factory's JSON too).
    todo = "todo"
    in_flight = "in-flight"
    ready = "ready"
    merged = "merged"
    # Named by the factory's ``.factory/run-state.json`` only — its in-progress
    # and failure-ish states, which the directory form cannot express.
    in_progress = "in_progress"
    in_part = "in_part"
    in_submilestone = "in_submilestone"
    flagged = "flagged"
    failed = "failed"
    needs_human = "needs_human"
    # Named by no source: no run-state source present, or it could not be read.
    unknown = "unknown"
    # Named by no source either — but for a different reason: a run-state source
    # WAS resolved and read, and it simply does not list this ticket. Distinct
    # from ``unknown`` so a caller cannot conflate "nothing to ask" with "asked,
    # and the answer is 'not listed'".
    absent = "absent"
