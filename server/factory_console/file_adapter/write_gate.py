"""The single write-path chokepoint: is a ticket mutable in its run-state?

The core v2 safety invariant is that a ticket may be edited ONLY when its factory
run-state is ``todo`` or ``unknown``. Read ``unknown`` as "NOTHING WAS SAID", which is
BROADER than "no source on disk" — it also covers a source that vanished before it
could be read, one whose content could not be parsed, and a source that lists nobody —
but which T80 amendment 4 narrowed to exactly that: a source that lists THIS ticket
under a status outside
:data:`~factory_console.file_adapter.run_state.FACTORY_STATUS_ALIASES` — or, in the
directory form, under a state subdirectory outside
:data:`~factory_console.file_adapter.run_state._MARKER_PRECEDENCE` (T92) — is no longer
``unknown`` but the refusing ``unreadable``, because something WAS said and this console
could not interpret it (see
:func:`~factory_console.file_adapter.run_state._resolve_json_state` for the four-way
split). Every other state — ``in-flight``/``ready``/``merged`` from the directory form,
``in_progress``/``in_part``/``in_submilestone``/``flagged``/``failed``/
``needs_human`` from the factory's ``run-state.json``, ``absent`` (a run-state
source resolved and does not list the ticket at all), and ``unreadable`` (the
information is UNAVAILABLE: a run-state source is THERE and could not be read at all,
or it lists this ticket under an entry this console cannot interpret) — is read-only, matching
how ``/factory-reconcile-plan`` treats them (see ``ARCHITECTURE.md`` "Factory
run-state source (read-only)"). :func:`_ensure_state_allowed` is the one
resolution-and-refusal site every gated write passes before touching disk —
:func:`ensure_mutable` and :func:`ensure_deletable` are its two named entry
points, differing ONLY in their allowlist — and :class:`TicketNotMutable` is the
ONE canonical error for the non-todo condition across the whole write path.
(``create_ticket`` passes no gate at all; see :data:`DELETABLE_STATES` for why
that is what forces delete's allowlist to be the wider one.)

DELETE is the single documented exception, and it is an exception of ALLOWLIST, not
of mechanism: :func:`ensure_deletable` runs the same resolution and raises the same
error, over :data:`DELETABLE_STATES` — :data:`MUTABLE_STATES` plus ``absent``. See
that constant for why (T80's amendment: an ungated ``create`` must not mint a ticket
the console cannot delete). ``absent`` stays out of :data:`MUTABLE_STATES`, so edit
is unaffected. ``unreadable`` is in NEITHER allowlist — it is the one state both
gates refuse without any recovery path here, because "I could not read the source"
cannot license a delete the way "the source does not track it" can (T80 amendment
2): the source we could not read may be the very thing that says a lane owns this
ticket.

This module REUSES the read-only, source-aware prober
(:func:`~factory_console.file_adapter.run_state.probe_ticket_state_with_reason`, the
reason-carrying form of ``probe_ticket_state_from_source`` — same single read, same
resolution, plus the description a refusal needs to name what the source said)
to resolve the state — it never re-implements run-state detection and never
writes to the run-state directory (it does no filesystem I/O of its own at all).
It reads through ``project.runStateSource``, not ``project.runStateDir``: a
JSON-sourced project has no run-state directory, and gating on the directory
there would see ``unknown`` for every ticket and wave through edits to tickets
the factory has already merged.
"""

from __future__ import annotations

from pathlib import Path

from factory_console.domain import Project, RunState
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.run_state import probe_ticket_state_with_reason

# The ONLY editable predicate: a ticket is mutable exactly when its resolved
# run-state is ``todo`` or ``unknown`` — the latter meaning "NOTHING WAS SAID": no
# source to ask, one that vanished, or one that names nobody. Broader than "no
# run-state source on disk", and since T80 amendment 4 no longer covering a source
# that DID say something about this ticket that could not be interpreted, which is
# ``unreadable`` and refuses (see the module docstring). Every other state is
# read-only. Single source of truth for the write-authorization decision — see
# ARCHITECTURE.md "Factory run-state source (read-only)".
MUTABLE_STATES = (RunState.todo, RunState.unknown)

# The DELETE-path allowlist: everything editable, PLUS ``absent``. Deliberately a
# separate tuple rather than a widened :data:`MUTABLE_STATES` — editing a ticket a
# resolved run-state source does not list stays refused (T80's rule), while deleting
# it is permitted, because ``create_ticket`` is ungated and a ticket the console
# just minted resolves ``absent`` the moment the project has a populated source the
# factory has not re-seeded. Refusing the delete too would leave a mistyped new
# ticket unrecoverable through the very UI that created it. Deleting a ticket the
# run-state does not track cannot orphan a run-state entry, so nothing the factory
# owns is at risk (T80 amendment, gap 2).
#
# ``unreadable`` is deliberately NOT here, and that asymmetry with ``absent`` is why
# the two are distinct states: ``absent`` licenses the delete because the source WAS
# read and provably does not track the ticket, so removing it cannot orphan anything.
# An unreadable source proves nothing — the entry saying a lane owns this ticket may
# be exactly what could not be read — so delete fails closed alongside edit (T80
# amendment 2). The recovery is to fix the source's permissions, not to widen this.
DELETABLE_STATES = (*MUTABLE_STATES, RunState.absent)


class TicketNotMutable(FactoryConsoleError):
    """A ticket cannot be edited because its factory run-state is not mutable.

    Raised by :func:`ensure_mutable` when a ticket's resolved :class:`RunState`
    is outside :data:`MUTABLE_STATES` (any state but ``todo``/``unknown``), and by
    :func:`ensure_deletable` when it is outside :data:`DELETABLE_STATES` (the same
    set plus ``absent``). This
    is the single canonical write-path error for the non-todo condition; mapped to
    HTTP 409 (the edit conflicts with the ticket's current lifecycle state).
    ``details`` echoes the (user-supplied) ``ticketId`` and the resolved
    ``runState`` — both already-known values, never a resolved filesystem path.
    That rule is scoped to ``details`` ALONE, and deliberately so since T80: the
    ``absent`` ``message`` below does carry a resolved path, and both fields ship in
    the same client-facing envelope (:func:`~factory_console.errors.to_error_response`).
    Read it as "``details`` stays a stable, machine-readable pair", NOT as "this
    error never discloses a path" — the path is already public on this API via
    ``GET /api/v1/project``'s ``runStateSource.path``.

    ``source_path`` is optional and used ONLY to phrase a distinct message for the
    TWO states that are about the SOURCE rather than about a lifecycle a factory lane
    put the ticket in — :attr:`RunState.absent` and :attr:`RunState.unreadable`. In
    both, an operator seeing the refusal needs to know WHICH file was consulted, since
    the answer is about "the file you are not looking at" (T80 step 4 mandates naming
    it for ``absent``; amendment 2 extends that to ``unreadable``). The messages
    are deliberately different, because the problems and their fixes are: ``absent``
    is "the source was read and does not list this ticket" (seed it, or delete it),
    while ``unreadable`` is "the information is unavailable" — and unlike ``absent``,
    ``unreadable`` refuses the delete too, so its message must not offer it.

    ``unclassifiable`` splits ``unreadable``'s message in two, for the same reason the
    state itself is split from ``absent``: a refusal has to name a fix the operator can
    act on. When it is set, the source was read fine and ONE CLAIM in it could not be
    interpreted, and it carries the description of what the source said — ``status
    'in_review'`` for the JSON form, ``state 'in_review'`` for a directory form marker
    under a state this console has no name for (T92). This branch is SOURCE-AGNOSTIC by
    construction and was before either form filled it from both: it phrases whatever
    description the prober hands it, and the prober owns which vocabulary that is
    — T80 amendment 4 requires the refusal to name the unrecognised
    value, because "the run-state says ``in_review``, which this console does not know"
    sends an operator to upgrade the console while "could not be read" sends them to
    chmod a path that is already readable. It is ``None`` for EVERY OTHER route to
    ``unreadable``, and that is stated as a RULE rather than as a list of them, because
    the directory form has grown four such routes across T92's two amendments and a list
    is what goes stale: this field is set only where the source was READ and named this id
    under something it could not interpret, so every route that arrived by NOT reading
    leaves it unset and falls to the generic "could not be read" message below. Some of
    those routes DO have a directory name to give (the state subdirectory that would not
    open, the run-state directory that would not enumerate) and withhold it here
    deliberately, because this branch phrases "your console is a version behind the
    factory" and that sentence is false when nothing was read; they carry the name in the
    operator-facing LOG instead.
    Every other state keeps the generic message; ``details`` is identical in shape
    in all four cases, so a client that switches on ``runState`` never has to parse prose
    — and note that ``details`` therefore does NOT distinguish the two ``unreadable``
    causes, which is deliberate: they are the same authorization answer, and only the
    remedy differs.
    """

    def __init__(
        self,
        ticket_id: str,
        run_state: RunState,
        *,
        source_path: Path | None = None,
        unclassifiable: str | None = None,
    ) -> None:
        if run_state is RunState.unreadable and source_path is not None and unclassifiable:
            # The SECOND route to ``unreadable``, and it must not borrow the first one's
            # prose: the file was read perfectly well, and what could not be interpreted
            # is one claim in it — one JSON entry (T80 amendment 4), or one marker under
            # a state directory this console has no name for (T92). Telling this operator
            # the source
            # "could not be read" sends them to chmod a path whose permissions are fine,
            # when the real fix is a console that knows the state the factory is now
            # writing. ``unclassifiable`` is the description of what the source actually
            # said — ``status 'in_review'``, ``state 'in_review'`` — because the amendment
            # requires the refusal to NAME the value rather than say "not tracked".
            #
            # The PHRASING is owned here, not by the domain: :class:`JsonRunState`
            # records what it read (``an entry with no status``) and passes no judgement
            # on it, and this layer is what turns that into a refusal an operator reads.
            # Keep it that way — a description that arrives pre-worded as a refusal
            # would put the write gate's vocabulary inside the parser.
            #
            # Guarded on TRUTHINESS rather than ``is not None`` deliberately: a blank
            # description would render "lists T01 under , which ..." — a malformed
            # sentence naming nothing — so an empty value degrades to the generic
            # "could not be read" message below instead of to broken prose.
            message = (
                f"The run-state at {source_path} lists {ticket_id} under "
                f"{unclassifiable}, which this console cannot interpret, so it will "
                f"not write to {ticket_id}"
            )
        elif run_state is RunState.unreadable and source_path is not None:
            # "will not write to it": unlike the ``absent`` branch below, BOTH writes
            # are refused here, so this message must not point at delete as a way out.
            # It names the source and the failure MODE (could not be read) rather than
            # the ticket's tracking status — an operator who reads "not known to the
            # run-state" goes looking for a missing ticket entry, when the fix is a
            # permission on the path named here.
            message = (
                f"The run-state at {source_path} could not be read, so the console "
                f"will not write to {ticket_id}"
            )
        elif run_state is RunState.absent and source_path is not None:
            # "will not EDIT it", not "will not write it". This branch is reachable
            # only from :func:`ensure_mutable`, because ``absent`` is inside
            # :data:`DELETABLE_STATES` — so by construction a delete of this same
            # ticket SUCCEEDS. The ticket's pre-amendment wording ("write") was
            # written before delete was widened; keeping it would tell an operator
            # the console refuses every write on a ticket it will happily delete,
            # which is the recovery path gap 2 exists to give them.
            message = (
                f"Ticket {ticket_id} is not known to the run-state at {source_path}, "
                "so the console will not edit it; it can still be deleted"
            )
        else:
            message = f"Ticket {ticket_id} is not editable in run-state '{run_state.value}'"
        super().__init__(
            code="ticket_not_mutable",
            message=message,
            status=409,
            details={"ticketId": ticket_id, "runState": run_state.value},
        )


def ensure_mutable(project: Project, ticket_id: str) -> RunState:
    """Return ``ticket_id``'s :class:`RunState` iff the ticket is editable.

    Resolves the state via the read-only, source-aware prober
    (:func:`~factory_console.file_adapter.run_state.probe_ticket_state_with_reason`
    over ``project.runStateSource``) and enforces the write invariant: a ``todo``/
    ``unknown`` ticket is mutable and its state is returned; any other state is
    read-only.

    Raises:
        TicketNotMutable: if the resolved state is not in :data:`MUTABLE_STATES`
            — HTTP 409.
        PathTraversal: if ``ticket_id`` is not a single path-safe segment AND the
            project's source is a run-state DIRECTORY. It propagates UNCHANGED
            from the prober, which validates the id only on the path it actually
            probes: with no source (or a JSON source, which joins no path) the
            prober answers ``unknown``/no-entry BEFORE the id is checked as a path
            segment, so no ``PathTraversal`` is raised. This gate authorizes by
            run-state and does no path I/O of
            its own, so it is NOT a path-safety guarantee: a downstream writer that
            turns ``ticket_id`` into a filesystem path MUST re-validate it at the
            point of use (as ``ticket_md``/``run_state`` do), never rely on this
            gate for path safety.
    """
    return _ensure_state_allowed(project, ticket_id, MUTABLE_STATES)


def ensure_deletable(project: Project, ticket_id: str) -> RunState:
    """Return ``ticket_id``'s :class:`RunState` iff the ticket may be DELETED.

    Identical to :func:`ensure_mutable` — same resolution, same
    :class:`TicketNotMutable` (409) — except that it authorizes against
    :data:`DELETABLE_STATES`, which additionally allows :attr:`RunState.absent`.
    Delete is the one write the console must still offer for a ticket a resolved
    run-state source does not list, because ``create_ticket`` is ungated: a ticket
    the console just created resolves ``absent`` in any project with a POPULATED
    source the factory has not re-seeded (a vacuous source answers the mutable
    ``unknown``), and without this gate the console could create a
    ticket it could never remove. Edit remains refused for ``absent`` via
    :func:`ensure_mutable`; the two allowlists are separate precisely so widening
    delete cannot widen edit. The widening stops at ``absent``: a source that could
    not be READ (:attr:`RunState.unreadable`) refuses the delete too, because it
    proves nothing about whether the factory tracks this ticket.

    Raises:
        TicketNotMutable: if the resolved state is not in :data:`DELETABLE_STATES`
            — HTTP 409.
        PathTraversal: exactly as :func:`ensure_mutable` (see its note); this gate
            is no more a path-safety guarantee than that one.
    """
    return _ensure_state_allowed(project, ticket_id, DELETABLE_STATES)


def _ensure_state_allowed(
    project: Project, ticket_id: str, allowed: tuple[RunState, ...]
) -> RunState:
    """Resolve ``ticket_id``'s state and return it iff it is in ``allowed``.

    The shared body of :func:`ensure_mutable` and :func:`ensure_deletable`: ONE
    resolution path and ONE error construction, so the edit and delete gates can
    differ only in their allowlist and never drift in how they resolve or how they
    refuse.

    It resolves through
    :func:`~factory_console.file_adapter.run_state.probe_ticket_state_with_reason`
    rather than the plain
    :func:`~factory_console.file_adapter.run_state.probe_ticket_state_from_source`
    because a refusal has to be able to NAME what the source said (T80 amendment 4).
    That is one read, not two — the reason falls out of the same parse as the state —
    so the permitting path pays nothing for it and the refusing path cannot report a
    value from different bytes than the ones it refused on.
    """
    run_state, unclassifiable = probe_ticket_state_with_reason(project.runStateSource, ticket_id)
    if run_state not in allowed:
        source_path = project.runStateSource.path if project.runStateSource else None
        raise TicketNotMutable(
            ticket_id, run_state, source_path=source_path, unclassifiable=unclassifiable
        )
    return run_state
