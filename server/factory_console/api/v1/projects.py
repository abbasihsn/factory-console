"""The ``GET /api/v1/projects`` list + ``GET /api/v1/projects/current`` endpoints.

The READ half of v3.0's registry surface: the two routes the SPA's project dropdown is
built from, and the two OpenAPI schemas its generated types come from. The mutations
(add, remove, switch) land in T113 so the write-token argument gets its own reviewable
diff; nothing here is gated by anything but the loopback boundary, exactly like every
other read route.

**Every row carries a ``condition``, never a boolean.** The union is T103's
:data:`~factory_console.domain.registry.RegistryEntryCondition`, established from disk
by T109's probe and joined onto the rows by T110's
:func:`~factory_console.store.entries.resolve_entries`. Both facts it separates matter
to a hosted console: "the working copy is not on this machine" (``path_missing``) and "I
could not look at it" (``unreadable``) send an operator to different places, and a
project whose ``.factory/`` is simply absent (``no_factory_dir``) is the ORDINARY state
of a fresh clone rather than a fault. The field is named ``condition`` in the domain
type, on the wire, and in the SPA's label map — there is no second name for it.

**No row is ever omitted, and no row is ever a bare error.** ``resolve_entries``
guarantees one entry per registry row, in order, and the probe is TOTAL, so a deleted or
unreadable project appears in the listing WITH the condition that names its state. A
listing that dropped it would tell the user they never registered it, which is a false
statement the console would be making about their own past action (``ARCHITECTURE.md``,
"The resolution invariant").

**The reserved ``session`` row.** ``factory-console PATH`` pins a root that is not a
registry row — it has no id, no ``addedAt``, and nothing durable behind it — and the
dropdown must still be populated on the very first boot so the SPA can offer "Add this
project" as an explicit act. So the listing PREPENDS one synthetic row with
``id="session"`` and ``registered=false`` whenever a pin exists. It cannot be expressed
as a :class:`~factory_console.domain.registry.RegistryEntry`: that model's ``project.id``
must match :data:`~factory_console.domain.registry.REGISTERED_PROJECT_ID_PATTERN`, which
``"session"`` deliberately cannot (that is what reserves the sentinel), and its
``addedAt`` is required. Hence two constructors on :class:`RegisteredProjectOut` rather
than one — see :meth:`RegisteredProjectOut.from_session`.

**Why ``/projects`` never fails over the selection and ``/projects/current`` never
404s.** Having nothing selected is the ordinary state of a fresh console, and the
listing is precisely what a user browses in order to leave that state — so neither route
raises the 409s :func:`~factory_console.api.deps.get_current_project_root` raises.
``/projects`` reports ``selected: false`` on every row; ``/projects/current`` answers 200
with ``selected: null`` and a NAMED ``reason`` from T111's
:data:`~factory_console.services.project_selection.SelectionFailure`, which the SPA
renders as a prompt rather than an error. :class:`RegistryUnreadable` is the one thing
that still propagates (as its 503): it is not a member of that union — see its own
docstring — because it says the CONSOLE cannot answer at all, not that the user's
selection is in a nameable state.

**One offload, not N.** The registry query is ``sqlite3`` and each row costs a handful of
``stat`` calls, so the selection read, the ``list_projects()`` query and the whole
``resolve_entries`` fold run TOGETHER inside a single ``anyio.to_thread.run_sync`` hop
per request (``ARCHITECTURE.md``, Cross-cutting → **Concurrency**). A per-row hop would
pay the thread hand-off N times for work that is a few syscalls each. Nothing is cached:
the SPA calls these on every switch and ``condition`` must be current, which is the same
reason :class:`~factory_console.file_adapter.project_condition.RealProjectConditionProbe`
memoises nothing.

The absolute host paths on the wire are the existing precedent, not a new disclosure:
``/health`` already publishes ``projectRoot`` under the same loopback trust boundary.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import (
    _probe_root,
    _read_registry,
    get_project_registry,
    get_selection_state,
)
from factory_console.domain.registry import (
    REGISTERED_PROJECT_ID_PATTERN,
    RegistryEntry,
    RegistryEntryCondition,
)
from factory_console.file_adapter.project_condition import (
    ProjectConditionProbe,
    RealProjectConditionProbe,
)
from factory_console.services.project_selection import (
    SESSION_PROJECT_ID,
    SelectionFailure,
    SelectionState,
)
from factory_console.store.entries import resolve_entries
from factory_console.store.paths import default_project_name
from factory_console.store.registry_protocol import ProjectRegistry

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names the
# routes and their OpenAPI tag (mirrors ``api/v1/tickets.py``).
router = APIRouter(tags=["projects"])

ProjectIdPath = Annotated[str, PathParam(pattern=REGISTERED_PROJECT_ID_PATTERN)]
"""A ``{project_id}`` path parameter validated at the FastAPI boundary.

The registry-id twin of :data:`~factory_console.api.deps.TicketIdPath`: it bounds what
may reach the registry FROM HTTP, so a malformed id becomes a 422 at the edge instead of
a lookup for a string no row could ever answer to. The id's FORM is T103's — the pattern
is imported verbatim from :data:`REGISTERED_PROJECT_ID_PATTERN` and never re-spelled
here — and because that pattern admits neither a path separator nor a ``.``, an id can
never name a parent directory.

**Neither route in this module takes a path parameter**, so nothing here uses it yet. It
is declared with the read routes on purpose rather than left to its first consumer: the
mutations (T113's ``DELETE /projects/{project_id}`` and the switch) are the endpoints
that will accept an id from a URL, and they must constrain it the same single way from
the first line they are written — a second spelling arriving alongside a mutation is
exactly how a write path ends up validating an id more loosely than a read path.
"""


class RegisteredProjectOut(BaseModel):
    """One row of the project switcher: a tracked project, its condition, its state.

    The DISCLOSURE BOUNDARY for a registry row, and the wire twin of
    :class:`~factory_console.domain.registry.RegistryEntry` — exactly as
    :class:`~factory_console.api.v1.runs.ProjectedArtifactRead` is for an artefact. The
    two constructors below name every field EXPLICITLY and never ``model_dump()`` the
    store entity, so a column the store track adds later (a last-opened timestamp, a
    cached branch name, an operator note) cannot reach the browser by accident. That
    matters more here than for most models: unlike a factory artefact, these rows are
    the console's OWN writable table, which is the kind of thing that grows columns.

    ``selected`` and ``registered`` are flattened onto the row rather than left for the
    client to derive. Both are answers only the server holds — the first needs the
    session's selection state, the second is the ``session`` sentinel's defining
    property — and a dropdown that had to compute either would re-derive server state
    from an id string.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    """The registry id, or :data:`SESSION_PROJECT_ID` for the reserved session row.

    Deliberately a bare ``str`` and NOT
    :data:`~factory_console.domain.registry.RegisteredProjectId`: this union of two id
    spaces is the whole point of the sentinel, and ``"session"`` cannot match the
    32-hex-digit pattern a registered id must (which is what makes the reservation safe
    by construction). A client tells them apart by ``registered``, never by parsing the
    id — and the inbound direction is still narrowed, by :data:`ProjectIdPath`."""

    name: str
    """The user's label for the project. For the session row, the pinned root's final
    component via :func:`~factory_console.store.paths.default_project_name` — the same
    default the store would mint if the operator registered it."""

    path: Path
    """The project root, as the row holds it. Serialised as a string by Pydantic (the
    precedent ``/health``'s ``projectRoot`` set) and NOT re-resolved here: re-resolving
    would silently address a different project than the row the user registered."""

    addedAt: datetime | None
    """When the row was registered, or ``None`` for the session row — which is an
    ephemeral property of one invocation and was never added to anything."""

    registered: bool
    """Whether this row is durable. ``False`` only for :data:`SESSION_PROJECT_ID`; it is
    what tells the SPA to offer "Add this project" instead of "Remove"."""

    selected: bool
    """Whether this row is what the console is currently serving, per
    :meth:`~factory_console.services.project_selection.SelectionState.current_id`."""

    condition: RegistryEntryCondition
    """What the console observed about ``path`` at READ time, resolved
    most-degraded-first per the union. Never a boolean, and never cached."""

    @classmethod
    def from_entry(cls, entry: RegistryEntry, *, selected: bool) -> RegisteredProjectOut:
        """Narrow one joined registry entry to the switcher row it discloses.

        The ONE constructor for a REGISTERED project, so every registered row on the
        wire is built by the same explicit field list. ``registered`` is ``True`` by
        construction: an entry exists only because a durable row does.

        ``selected`` is passed in rather than derived, because an entry knows nothing
        about the session — the caller holds the selected id and compares once for the
        whole listing instead of re-reading the selection per row.
        """
        return cls(
            id=entry.project.id,
            name=entry.project.name,
            path=entry.project.path,
            addedAt=entry.project.addedAt,
            registered=True,
            selected=selected,
            condition=entry.condition,
        )

    @classmethod
    def from_session(
        cls,
        pinned_root: Path,
        *,
        condition: RegistryEntryCondition,
        selected: bool,
    ) -> RegisteredProjectOut:
        """Build the reserved ``session`` row for a ``factory-console PATH`` boot.

        A second constructor rather than a synthetic
        :class:`~factory_console.domain.registry.RegistryEntry`, because that model
        cannot represent this row and should not be bent into representing it: its
        ``project.id`` must match
        :data:`~factory_console.domain.registry.REGISTERED_PROJECT_ID_PATTERN` (which
        ``"session"`` cannot, by design) and its ``addedAt`` is required (which a pin has
        no honest value for). Faking either — a zero uuid, ``datetime.now()`` — would put
        a value on the wire that reads as durable when nothing durable exists.

        ``condition`` is passed in, PROBED like any other row's rather than assumed
        ``ok``. Booting proves only that the root was discoverable at boot, and this
        answer is read on every request: the directory may have been deleted or made
        unreadable since, and — the common case — a fresh clone's ``.factory/`` is
        absent, which is ``no_factory_dir`` and is exactly what the dropdown should say.
        Defaulting to ``ok`` here would make the one row present on every boot the only
        row that lies about its state. Taking the value as an argument rather than a
        probe keeps this model free of I/O; the caller does the probing inside its single
        worker-thread hop.
        """
        return cls(
            id=SESSION_PROJECT_ID,
            name=default_project_name(pinned_root),
            path=pinned_root,
            addedAt=None,
            registered=False,
            selected=selected,
            condition=condition,
        )


class ProjectListResponse(BaseModel):
    """Envelope for the projects list: the switcher rows and their count.

    The ``{items, total}`` shape ``/tickets``, ``/search`` and ``/runs`` already use, so
    a client that unwraps ``items`` for three lists does not special-case a fourth.
    ``total`` is ``len(items)`` — there is no filtering and no pagination, and the count
    INCLUDES the session row when one is present, because that row is a row of the
    dropdown like any other.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[RegisteredProjectOut]
    total: int


class CurrentSelectionResponse(BaseModel):
    """What the console is serving right now — or the named reason it is serving nothing.

    Exactly one of ``selected`` and ``reason`` is set, the same one-of discipline
    :class:`~factory_console.api.v1.runs.ProjectedArtifactRead` keeps between its
    ``data`` and ``reason``. The invariant is not restated as a validator because
    :func:`get_current` is this model's only constructor and builds it in one place from
    a two-branch union; a second copy of the rule would have one owner and two homes.

    ``reason`` is T111's :data:`SelectionFailure`, imported verbatim so ``/health``'s
    ``selectionReason``, the read endpoints' 409 codes and this field cannot come to
    disagree about spelling.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected: RegisteredProjectOut | None = None
    reason: SelectionFailure | None = None


def _list_rows(
    selection: SelectionState,
    registry: ProjectRegistry | None,
    probe: ProjectConditionProbe,
) -> list[RegisteredProjectOut]:
    """Compose the whole switcher listing — SYNCHRONOUS, so one hop covers all of it.

    Everything blocking the listing needs happens here and nowhere else: the selection
    read (which reads THROUGH to the persisted selection when no session pin is set),
    the registry query, and one condition probe per row plus one for the pinned root. The
    caller runs this on a worker thread exactly once per request, which is the
    single-offload rule ``store/entries.py`` states the fold must be called under.

    A ``None`` registry is PINNED MODE and yields no registered rows — a valid
    configuration (every pre-v3 app), not an error, so the listing is simply the session
    row. The session row is PREPENDED, so the boot-time project is what the dropdown
    opens on.
    """
    selected_id = selection.current_id()
    rows = [] if registry is None else registry.list_projects()
    items = [
        RegisteredProjectOut.from_entry(entry, selected=entry.project.id == selected_id)
        for entry in resolve_entries(rows, probe)
    ]
    if selection.pinned_root is not None:
        items.insert(
            0,
            RegisteredProjectOut.from_session(
                selection.pinned_root,
                condition=probe.probe(selection.pinned_root),
                selected=selected_id == SESSION_PROJECT_ID,
            ),
        )
    return items


def _resolve_current(
    selection: SelectionState,
    registry: ProjectRegistry | None,
    probe: ProjectConditionProbe,
) -> RegisteredProjectOut | SelectionFailure:
    """Resolve the selection to its row, or to the named reason there is none.

    The same precedence :func:`~factory_console.api.deps.get_current_project_root`
    applies — session pin, nothing selected, not registered, path unavailable — and
    deliberately the same MECHANISM for the last step: it calls that module's
    :func:`~factory_console.api.deps._probe_root`, so this endpoint's ``reason`` cannot
    come to disagree with the 409 every other endpoint answers for the same selection.
    A second, independently-derived classification here would eventually let the SPA
    render a healthy header over panels that are all refusing.

    It RETURNS rather than raises, which is the one substantive difference. Nothing here
    is an error condition: a fresh console with no selection is the state this endpoint
    exists to report, so every failure comes back as a :data:`SelectionFailure` member
    for the 200 envelope. :class:`RegistryUnreadable` is not among them and is not caught
    — the caller's ``_read_registry`` still raises it as a 503, because "I cannot read my
    own store" is a statement about the console's health rather than a state of the
    user's selection (see that class).

    Two filesystem answers are needed for a healthy selected row and they come from
    different classifiers, on purpose. ``_probe_root`` decides SERVABILITY in the three
    terms the other endpoints refuse in (present / missing / unreadable); the probe
    decides the five-way ``condition`` the row DISPLAYS (which additionally separates
    ``not_a_project`` and ``no_factory_dir``, neither of which is a selection failure —
    a ``.factory``-less project reads fine, and a directory with no manifest is left to
    the endpoints' own discovery errors). The probe runs only after servability is
    established, so a missing or unreadable path costs one classification, not two.

    The pinned root is served with NO servability probe, matching step 1 of the
    precedence: boot-time discovery already established it, and the pin must resolve even
    where a probe would refuse. Its ``condition`` is still probed, because that is a
    display fact read fresh on every request.
    """
    selected_id = selection.current_id()
    if selected_id == SESSION_PROJECT_ID and selection.pinned_root is not None:
        return RegisteredProjectOut.from_session(
            selection.pinned_root,
            condition=probe.probe(selection.pinned_root),
            selected=True,
        )
    if selected_id is None or selected_id == SESSION_PROJECT_ID:
        # A session id without a pin is the pathless, never-switched boot: the sentinel
        # names a root that does not exist, which is "nothing selected".
        return "no_selection"
    if registry is None:
        # Pinned mode cannot name another project, so an id here means the selection
        # outlived the registry it came from. The pin is NOT substituted.
        return "selected_project_not_registered"
    row = registry.get_project(selected_id)
    if row is None:
        return "selected_project_not_registered"
    failure = _probe_root(row.path)
    if failure is not None:
        return failure
    return RegisteredProjectOut.from_entry(
        RegistryEntry(project=row, condition=probe.probe(row.path)), selected=True
    )


@router.get("/projects")
async def list_projects(
    registry: ProjectRegistry | None = Depends(get_project_registry),
    selection: SelectionState = Depends(get_selection_state),
) -> ProjectListResponse:
    """Return every project the switcher offers, session row first, with its condition.

    Delegates the whole composition to :func:`_list_rows` inside a SINGLE
    ``anyio.to_thread.run_sync`` hop — the selection read, the ``sqlite3`` query and the
    per-row ``stat``s together, never one hop per row. ``functools.partial`` binds the
    arguments because ``run_sync`` passes positionals only.

    This route never fails over the selection: with nothing selected, every row simply
    reports ``selected: false``. That is the point of it — the listing is what a user
    browses in order to choose, so raising the 409s
    :func:`~factory_console.api.deps.get_current_project_root` raises would deny them the
    screen that fixes the condition. A :class:`RegistryUnreadable` from the store still
    propagates as its 503 through ``_read_registry``, which is the honest answer when the
    console cannot read its own table at all.

    The condition probe is instantiated here rather than injected through ``app.state``.
    :class:`~factory_console.file_adapter.project_condition.RealProjectConditionProbe` is
    stateless, takes no arguments and caches nothing, and no DI seam for it exists yet;
    inventing one for a single call site would add app wiring this contract does not
    need. Tests reach the seam by overriding these dependencies or by pointing real
    ``tmp_path`` directories at the real probe, which is the more faithful test anyway.
    """
    items = await _read_registry(
        partial(_list_rows, selection, registry, RealProjectConditionProbe())
    )
    return ProjectListResponse(items=items, total=len(items))


@router.get("/projects/current")
async def get_current(
    registry: ProjectRegistry | None = Depends(get_project_registry),
    selection: SelectionState = Depends(get_selection_state),
) -> CurrentSelectionResponse:
    """Return the selected project, or ``selected: null`` with the named ``reason``.

    A 200 in both cases, never a 404: having nothing selected is the ordinary state of a
    fresh console, and the SPA renders the reason as a prompt. The resolution — and the
    argument for why it reuses the selection seam's own servability probe rather than
    re-deriving one — is in :func:`_resolve_current`; like the listing, it runs in one
    ``anyio.to_thread.run_sync`` hop through ``_read_registry``, so a
    :class:`RegistryUnreadable` still surfaces as a 503.

    Exactly one of the two fields is set, because the resolution returns exactly one of a
    row or a reason and each populates its own field.
    """
    resolved = await _read_registry(
        partial(_resolve_current, selection, registry, RealProjectConditionProbe())
    )
    if isinstance(resolved, RegisteredProjectOut):
        return CurrentSelectionResponse(selected=resolved)
    return CurrentSelectionResponse(reason=resolved)
