"""The lifecycle state of a ticket, as named by whichever run-state source said so.

Design decision — a member's string VALUE mirrors the name the SOURCE that
contributed it uses, not one canonical spelling:

- The legacy run-state *directory* (``.factory/run-state/<state>/<id>``, see
  ``ARCHITECTURE.md`` "Factory run-state source (read-only)") names ``todo``,
  ``in-flight`` (hyphenated), ``ready``, ``merged``.
- The factory's ``.factory/run-state.json`` names the nine ``FAC_STATES``:
  ``todo in_progress ready in_part in_submilestone merged flagged failed
  needs_human`` (underscored). There is no ``in-flight`` in the factory at all —
  it exists here only because the directory form names it.

``unknown`` belongs to neither source: it is the "NOTHING WAS SAID" catch-all, which
is BROADER than "no run-state source on disk". It also
covers a source whose bytes were read but could not be PARSED or understood
(unparseable JSON, no ``tickets`` object, non-UTF-8 content) — a document that
resolved into nothing names no ticket, so it claims nothing about the one being
asked about — and a source that
resolved and lists NO ticket at all (a VACUOUS source — an empty marker
directory, or a ``run-state.json`` whose ``tickets`` object parsed and is empty;
a source that names nobody says nothing about anybody). It does NOT cover a ticket
the source DOES list under a status outside
:data:`~factory_console.file_adapter.run_state.FACTORY_STATUS_ALIASES`: that used to
land here, and T80 amendment 4 moved it to the refusing ``unreadable`` below, because
an entry naming this ticket is a CLAIM this console could not interpret, and only
silence may be mutable.
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
CLOSED. The information this console needed is UNAVAILABLE, in either of the two ways
T80's restated resolution invariant recognises (amendment 4): the source EXISTS and
could not be READ at all — a marker directory that raises ``EACCES`` on enumeration, a
``run-state.json`` whose bytes cannot be read — or it was read and could not be
INTERPRETED, i.e. it lists this ticket under a status outside the alias table, under a
status that is not a string, under an entry that is not an object at all, or — in the
DIRECTORY form — under a state subdirectory outside the four the marker-precedence walk
can name (T92), which is that
form's mirror of the unrecognised ``status`` and refuses ahead of any recognised marker
for the same id, since a state this console cannot name has a precedence it cannot rank
either. Amendment
4 widened the WORDING of the rule rather than adding a fifth unnamed state, because
both are the same authorization answer: this console has nothing it may act on. Where
they differ is the REMEDY, and that lives in the refusal's prose
(:class:`~factory_console.file_adapter.write_gate.TicketNotMutable` names the
unrecognised value) — "fix the source's permissions" and "your console does not know
the status the factory is writing" are different instructions.
It differs from ``unknown`` by WHY there is no answer:
``unknown`` is "nothing was said" (no source, a source that
names nobody, a source whose whole document was read and made no sense, so it named
nobody either), ``unreadable``
is "something was said and I could not make it out". It differs from ``absent`` by what
the source said: ``absent`` is a source that answered "not listed", ``unreadable`` is a
source whose answer this console cannot use.
Something claims, and we could not see what — so ``unreadable`` is
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
    # Nothing was said: no run-state source present, one that vanished before it
    # could be read, one whose whole document was read but could not be parsed or
    # understood (so it named nobody), or one that resolved and lists no ticket at
    # all. See the module docstring — this is deliberately broader than "no source on
    # disk", and deliberately NARROWER than it was, twice over: a source that exists
    # and refuses to be READ is ``unreadable`` below (amendment 2), and so is an entry
    # that names THIS ticket under a status this console cannot classify (amendment 4
    # — an entry naming the ticket is a claim, and a claim we could not interpret is
    # not silence).
    unknown = "unknown"
    # Named by no source either — but for a different reason: a run-state source
    # WAS resolved and read, it lists at least one ticket, and it simply does not
    # list this one. Distinct from ``unknown`` so a caller cannot conflate "no
    # answer to trust" with "asked, and the answer is 'not listed'".
    absent = "absent"
    # Named by no source for a third reason: the information is UNAVAILABLE. Either a
    # run-state source is THERE and could not be read (EACCES on a marker directory, an
    # I/O error on the JSON file), or it was read and what it says about this ticket could
    # not be interpreted (a status outside the alias table, a non-string status, an entry
    # that is not an object, or a marker under a state subdirectory this console has no
    # name for). So neither "not listed" nor "nothing to find" is honest — the
    # answer is "I could not look, or I looked and did not understand" (amendments 2 and
    # 4). The only unnamed state the write gate refuses for BOTH edit and
    # delete: an unreadable source may be hiding a ``merged`` marker, so failing
    # open here would grant write access precisely because we could not check.
    unreadable = "unreadable"
