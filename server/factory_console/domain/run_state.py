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
covers a source whose bytes were read but could not be PARSED or understood
(unparseable JSON, no ``tickets`` object, non-UTF-8 content), a source that
resolved and lists NO ticket at all (a VACUOUS source — an empty marker
directory, or a ``run-state.json`` whose ``tickets`` object parsed and is empty;
a source that names nobody says nothing about anybody), and a ticket the source
DOES list under a status outside
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

``unreadable`` is the third of these unnamed states, and it is the one that fails
CLOSED. A run-state source EXISTS and the console could not read it at all — a
marker directory that raises ``EACCES`` on enumeration, a ``run-state.json`` whose
bytes cannot be read. It differs from ``unknown`` by WHY there is no answer:
``unknown`` is "I looked and there is nothing to find" (no source, a source that
names nobody, a source whose content was read and made no sense), ``unreadable``
is "I could not look". It differs from ``absent`` by what the source said:
``absent`` is a source that answered "not listed", ``unreadable`` is a source that
did not answer. Something claims, and we could not see what — so ``unreadable`` is
in NEITHER :data:`~factory_console.file_adapter.write_gate.MUTABLE_STATES` nor
:data:`~factory_console.file_adapter.write_gate.DELETABLE_STATES`: unlike
``absent``, where "not tracked by the factory" makes a delete provably harmless,
an unreadable source may be hiding a ``merged`` marker, so both writes are refused
(T80 amendment 2). A source that VANISHED between discovery and the read is NOT
this state — nothing is there to be unreadable, so it resolves ``unknown`` and
stays mutable, exactly like a project with no source at all.

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
    # No answer to trust: no run-state source present, one that vanished before it
    # could be read, one whose content was read but could not be parsed or
    # understood, one that resolved but lists no ticket at all, or an entry whose
    # status this console does not recognise. See the module docstring — this is
    # deliberately broader than "no source on disk", and deliberately NARROWER than
    # it was: a source that exists and refuses to be READ is ``unreadable`` below.
    unknown = "unknown"
    # Named by no source either — but for a different reason: a run-state source
    # WAS resolved and read, it lists at least one ticket, and it simply does not
    # list this one. Distinct from ``unknown`` so a caller cannot conflate "no
    # answer to trust" with "asked, and the answer is 'not listed'".
    absent = "absent"
    # Named by no source for a third reason: a run-state source is THERE and could
    # not be read (EACCES on a marker directory, an I/O error on the JSON file), so
    # neither "not listed" nor "nothing to find" is honest — the answer is "I could
    # not look". The only unnamed state the write gate refuses for BOTH edit and
    # delete: an unreadable source may be hiding a ``merged`` marker, so failing
    # open here would grant write access precisely because we could not check.
    unreadable = "unreadable"
