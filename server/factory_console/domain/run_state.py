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

``unknown`` belongs to neither source: it is the "no answer this console can
trust" catch-all, which is BROADER than "no run-state source on disk". It also
covers a source that could not be read or parsed, a source that resolved and
lists NO ticket at all (a VACUOUS source — an empty marker directory, or a
``run-state.json`` whose ``tickets`` object parsed and is empty; a source that
names nobody says nothing about anybody), and a ticket the source DOES list under
a status outside
:data:`~factory_console.file_adapter.run_state.FACTORY_STATUS_ALIASES`.
``absent`` is different again: a run-state source WAS resolved, lists at least one
ticket, and does not list the ticket being asked about — a ticket added to
``tickets.json`` by hand after the last factory run, or a mistyped id. The two
must never collapse into each other: ``unknown`` is "no answer to trust";
``absent`` is "the source answered, and its answer is 'not listed'". Neither is
NAMED by a source, but they are NOT treated alike by the write gate: ``unknown``
stays editable, because a project with no run-state source at all must remain
fully usable in the console, while ``absent`` is refused an edit. ``absent`` is
still DELETABLE (:data:`~factory_console.file_adapter.write_gate.DELETABLE_STATES`),
since an ungated ``create`` must not mint a ticket the console can never remove.
Because
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
    # No answer to trust: no run-state source present, one that could not be read
    # or parsed, one that resolved but lists no ticket at all, or an entry whose
    # status this console does not recognise. See the module docstring — this is
    # deliberately broader than "no source on disk".
    unknown = "unknown"
    # Named by no source either — but for a different reason: a run-state source
    # WAS resolved and read, it lists at least one ticket, and it simply does not
    # list this one. Distinct from ``unknown`` so a caller cannot conflate "no
    # answer to trust" with "asked, and the answer is 'not listed'".
    absent = "absent"
