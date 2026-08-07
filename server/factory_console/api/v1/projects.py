"""The ``/api/v1/projects`` registry endpoints — the switcher's reads and its writes.

The whole of v3.0's registry surface, one endpoint family in one module: the two READ
routes the SPA's project dropdown is built from (``GET /projects``,
``GET /projects/current``) and the three MUTATIONS that fill it (``POST /projects``,
``DELETE /projects/{project_id}``, ``PUT /projects/current``).

**Only the mutations are gated.** Each of the three attaches
``Depends(require_write_token)`` and names the published scheme in its own
``openapi_extra`` — the same pair :mod:`factory_console.api.v1.tickets_write` uses,
because ``require_write_token`` is a plain dependency FastAPI cannot infer a scheme
from. The gate is attached PER ROUTE here rather than on the router (as the ticket
writes do), because this router also carries the two read routes, which must stay
header-free: they disclose nothing the loopback boundary does not already permit, and
they are how an operator diagnoses a bad selection.

The token is defence-in-depth BEHIND the loopback boundary, and ``POST /projects`` is
why the reads/writes split falls here. It makes the console open an ARBITRARY absolute
path on this machine and then serve that project's contents to anyone who can reach
the port — an arbitrary-path read primitive, strictly larger than editing one ticket in
an already-chosen project — and it is reachable by CSRF, since the console runs no CORS
policy and no CSRF token. ``PUT /projects/current`` is gated for a narrower reason: it
changes what EVERY read endpoint returns for every client, so an unguarded switch is a
way to make an operator read the wrong project's board while believing it is theirs.
``DELETE`` destroys durable console state, which is the ordinary reason.

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
memoises nothing. Every hop — read or write — goes through
:func:`~factory_console.api.deps._read_registry`, so a store the console cannot reach at
all is the SAME named 503 on a mutation as on a listing rather than a bare 500.

**The one thing that is deliberately NOT offloaded is**
:meth:`~factory_console.services.project_selection.SelectionState.select`. It must run
on the EVENT-LOOP thread, and that is a correctness constraint rather than a
convenience: ``select()`` invokes its on-change hooks synchronously on the calling
thread, and the only real subscriber —
:func:`factory_console.app._watcher_retarget_hook` — branches on whether a loop is
running. On the loop it defers the watcher swap to a task and returns immediately
(nothing blocks the request); on a worker thread it finds no loop, runs the swap inline,
and the rebuild half cannot start a ``RealFileWatcher`` because that captures the
running loop there is none of — so every project switch would silently degrade the
console to watcher-less. What stays on the loop is therefore one small ``UPDATE`` of a
single ``console_state`` row, which is the trade named explicitly on
:func:`select_current` and :func:`remove_project`.

The absolute host paths on the wire are the existing precedent, not a new disclosure:
``/health`` already publishes ``projectRoot`` under the same loopback trust boundary.
"""

from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import (
    _guard_registry_io,
    _probe_root,
    _read_registry,
    get_file_adapter,
    get_project_registry,
    get_selection_state,
)
from factory_console.api.write_token import WRITE_TOKEN_SCHEME_NAME, require_write_token
from factory_console.domain.registry import (
    REGISTERED_PROJECT_ID_PATTERN,
    RegistryEntry,
    RegistryEntryCondition,
)
from factory_console.errors import FactoryConsoleError
from factory_console.file_adapter.project_condition import (
    ProjectConditionProbe,
    RealProjectConditionProbe,
)
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.services.project_selection import (
    SESSION_PROJECT_ID,
    SelectionFailure,
    SelectionState,
)
from factory_console.store.entries import resolve_entries
from factory_console.store.paths import canonical_project_path, default_project_name
from factory_console.store.registry_protocol import ProjectNotRegistered, ProjectRegistry

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names the
# routes and their OpenAPI tag (mirrors ``api/v1/tickets.py``).
router = APIRouter(tags=["projects"])

ProjectIdPath = Annotated[
    str,
    PathParam(pattern=f"(?:{REGISTERED_PROJECT_ID_PATTERN})|(?:^{SESSION_PROJECT_ID}$)"),
]
"""A ``{project_id}`` path parameter validated at the FastAPI boundary.

The registry-id twin of :data:`~factory_console.api.deps.TicketIdPath`: it bounds what
may reach the registry FROM HTTP, so a malformed id becomes a 422 at the edge instead of
a lookup for a string no row could ever answer to. Its consumer is
``DELETE /projects/{project_id}``, the one route in this module that takes an id from a
URL at all.

**It admits BOTH id spaces this module publishes** — a registered id and the reserved
:data:`SESSION_PROJECT_ID` — composed from
:data:`REGISTERED_PROJECT_ID_PATTERN` and the sentinel VERBATIM, so neither form is
re-spelled here and the registered half cannot drift from the one T103 declares.

Admitting the sentinel is deliberate, not laxity. ``session`` is an id a client
legitimately HOLDS: every listing publishes it as a row, and ``DELETE`` has a named,
specific answer for it (:class:`SessionProjectNotRemovable`, 409 — it is not a registry
row and was never added). Narrowed to registered ids alone, that request would be a 422
``validation_error``, which tells the SPA the id is MALFORMED — a false statement about
an id the console itself just handed out, and one a client cannot branch on to explain
why the row has no Remove button. Widening the edge by exactly one known value is what
lets the refusal name its own reason.

Nothing else is admitted: an id that is neither 32 lowercase hex digits nor the sentinel
is rejected at the boundary, and because neither alternative can contain a path
separator or a ``.``, an id can never name a parent directory.
"""

# ``require_write_token`` is a plain dependency rather than a ``SecurityBase``, so
# FastAPI cannot derive a ``security`` requirement from it. Each gated operation
# therefore names the scheme ``publish_write_token_scheme`` publishes — the same
# constant ``tickets_write.py`` declares for the same reason — or the OpenAPI document
# would describe a header that no operation actually requires.
_WRITE_TOKEN_SECURITY: dict[str, Any] = {"security": [{WRITE_TOKEN_SCHEME_NAME: []}]}


class SessionProjectNotRemovable(FactoryConsoleError):
    """The reserved ``session`` row cannot be removed: it was never registered.

    Status 409 rather than 404, for the same reason
    :class:`~factory_console.store.registry_protocol.DuplicateProjectPath` is: the
    request is well-formed and names a row the console really does publish — it is the
    STATE of that row (ephemeral, unregistered, owned by this invocation of
    ``factory-console PATH``) that makes the operation impossible. A 404 would claim the
    id names nothing, which is exactly the opposite of what the listing says, and would
    send an operator hunting for a row that is right there in their dropdown.

    Home is this module, not
    :mod:`factory_console.store.registry_protocol`, because its raise site is this ONE
    endpoint and its subject is not a registry concern at all: the registry has no
    opinion about the sentinel — no row ever carries that id — so the port would be
    declaring an error no implementation can raise. The sentinel is an edge-level
    projection, and its refusal belongs at the edge that projects it.
    """

    def __init__(self) -> None:
        super().__init__(
            code="session_project_not_removable",
            message=(
                "The session project is not a registered project and cannot be "
                "removed; it lasts only for this server invocation."
            ),
            status=409,
            details={"projectId": SESSION_PROJECT_ID},
        )


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


class AddProjectRequest(BaseModel):
    """The body of ``POST /projects``: which directory to start tracking, and its label.

    ``extra="forbid"`` because this is the request that makes the console open an
    arbitrary path: a key the server does not understand is far more likely to be a
    caller sending a field this contract never agreed to than a harmless typo, and
    silently dropping it would let a client believe an option took effect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    """The project root to register, as the caller typed it.

    A ``str`` and deliberately NOT a :class:`~pathlib.Path`: the rule for turning input
    into a row's identity is :func:`~factory_console.store.paths.canonical_project_path`
    — blank, still-relative and unresolvable are its named 400 — and Pydantic coercing
    the string first would pre-empt it, since ``Path("")`` is ``Path(".")`` and a blank
    path would arrive at the store as the server's working directory. The one rule runs
    on the one unmodified input."""

    name: str | None = None
    """The user's label, or ``None`` to take the directory name.

    The default is applied by
    :meth:`~factory_console.store.registry_protocol.ProjectRegistry.add_project` (via
    :func:`~factory_console.store.paths.default_project_name`), not here: the store owns
    what an unnamed row is called, and defaulting at the edge as well would give one
    rule two homes that could disagree for a path with no final component."""


class SelectProjectRequest(BaseModel):
    """The body of ``PUT /projects/current``: which row the console should serve next.

    A body rather than a path parameter because the resource being replaced is
    ``/projects/current`` — "what is selected" — and the id is its new VALUE, not its
    address. ``extra="forbid"`` for the same reason as :class:`AddProjectRequest`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    projectId: str
    """A registry id, or :data:`SESSION_PROJECT_ID` for the pinned session row.

    A bare ``str`` and NOT
    :data:`~factory_console.domain.registry.RegisteredProjectId`, for the same reason
    :attr:`RegisteredProjectOut.id` is one: the sentinel is a legal target here (a
    ``factory-console PATH`` boot must be able to switch BACK to the pinned root) and it
    cannot match the 32-hex-digit pattern a registered id must. The two id spaces are
    separated by :func:`select_current`, which is where the sentinel's one precondition
    — a pin actually exists — is checked."""


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


def _current_response(
    resolved: RegisteredProjectOut | SelectionFailure,
) -> CurrentSelectionResponse:
    """Put a resolution into whichever of the envelope's two fields it belongs in.

    The ONE place :class:`CurrentSelectionResponse`'s one-of invariant is established,
    shared by the read route and the switch so the two cannot come to disagree about
    which field a reason lands in. That invariant is why the model carries no validator
    (see its docstring): the rule has one owner, and this is it.
    """
    if isinstance(resolved, RegisteredProjectOut):
        return CurrentSelectionResponse(selected=resolved)
    return CurrentSelectionResponse(reason=resolved)


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
    return _current_response(resolved)


# --------------------------------------------------------------------------- #
# The write half: add, remove, switch — all three write-token gated
# --------------------------------------------------------------------------- #


def _require_registry(registry: ProjectRegistry | None) -> ProjectRegistry:
    """Return the bound registry, or raise because there is nothing to mutate.

    A ``None`` registry is PINNED MODE, and the READ routes treat it as the valid
    configuration it is — a listing of just the session row, not an error (see
    :func:`~factory_console.api.deps.get_project_registry`). A MUTATION has no such
    degraded answer available: "add this project", "remove that row" and "select this id"
    all name durable state, and an app with no store cannot hold any of it, so the only
    honest responses are a raise or a lie.

    It raises :class:`RuntimeError` — a 500 — rather than a 4xx, deliberately, and the
    precedent is exact: :func:`~factory_console.api.deps.get_file_writer` does the same
    for a writer-less app, which is equally a valid configuration right up until a write
    route is called on it. The client did nothing wrong and has nothing to fix, so no
    4xx is true; what is true is that this deployment was wired without the seam its own
    route needs, which is a fact about the operator's wiring and belongs in the server's
    log with a stack trace rather than in a response body the SPA would render as user
    error.
    """
    if registry is None:
        raise RuntimeError(
            "No ProjectRegistry bound on app.state.project_registry, so there is "
            "nothing for a registry mutation to write to; build the app with "
            "create_app(project_registry=...)."
        )
    return registry


def _register_project(
    path: str,
    name: str | None,
    adapter: FileAdapter,
    registry: ProjectRegistry,
    probe: ProjectConditionProbe,
) -> RegisteredProjectOut:
    """Validate a candidate path, insert its row, and build the row's wire shape.

    SYNCHRONOUS, so the caller's single ``anyio.to_thread.run_sync`` hop covers the
    whole handler: a canonicalisation, a discovery walk, a ``sqlite3`` ``INSERT`` and a
    condition probe, which is four blocking steps and one thread hand-off.

    The order is the argument. Canonicalisation comes FIRST, because it is what turns
    input into an identity: a relative path handed to discovery would be resolved
    against the SERVER's working directory — something the caller cannot see and did not
    choose — so it must be refused (:class:`InvalidProjectPath`, 400) before anything
    looks at the filesystem. Discovery comes second, so a path that is not an App
    Factory project is refused before a row exists for it. The insert comes last and is
    the only step that changes anything, so both refusals leave the registry untouched.

    Nothing is caught here. :class:`~factory_console.store.paths.InvalidProjectPath`
    (400), :class:`~factory_console.file_adapter.discovery.ProjectNotFound` (404),
    :class:`~factory_console.file_adapter.manifest.MalformedManifest` (500) and
    :class:`~factory_console.store.registry_protocol.DuplicateProjectPath` (409) each
    already carry the status and code the registered handler renders, and re-mapping any
    of them here would give one condition two spellings.

    The DUPLICATE check is the store's, not a pre-flight
    :meth:`~factory_console.store.registry_protocol.ProjectRegistry.find_by_path`: the
    ``UNIQUE`` index on ``projects.path`` is the authority the port names, and a
    ``SELECT``-then-``INSERT`` here would be a race with any concurrent writer while
    also duplicating the rule.

    ``condition`` is PROBED rather than assumed ``ok``, exactly as every listed row's
    is. Registration deliberately does not require the path to exist (a laptop with the
    drive unplugged must still be able to add its project), so the new row's honest
    condition may be ``path_missing`` or ``no_factory_dir`` from the moment it is
    created, and the SPA renders the row it just added like any other.

    ``selected=False`` is true by construction and not a policy decision made here:
    :meth:`~factory_console.store.registry_protocol.ProjectRegistry.add_project` never
    auto-selects, and the id it mints is fresh, so no selection can already name it.
    """
    root = canonical_project_path(path)
    adapter.load_project(root)
    row = registry.add_project(root, name)
    return RegisteredProjectOut.from_entry(
        RegistryEntry(project=row, condition=probe.probe(row.path)), selected=False
    )


def _remove_registered_project(
    project_id: str,
    registry: ProjectRegistry,
    selection: SelectionState,
) -> bool:
    """Delete one registry row, reporting whether it was the SELECTED one.

    Synchronous, for the caller's one hop: the selection read and the ``DELETE`` are
    both ``sqlite3``.

    "Was it selected?" is answered BEFORE the removal, because afterwards there is
    nothing left to compare against — the row is gone and the schema's
    ``ON DELETE SET NULL`` has already cleared the persisted selection, so a check made
    after the fact could not tell "this delete cleared it" from "nothing was selected
    anyway". The boolean travels back to the handler, which is the only place that may
    move the PROCESS-LOCAL selection (see :func:`remove_project` for why that step
    cannot happen on this thread).

    Unknown ids are turned into :class:`ProjectNotRegistered` (404) HERE rather than
    answered idempotently, because
    :meth:`~factory_console.store.registry_protocol.ProjectRegistry.remove_project`
    returns ``False`` instead of raising precisely so the edge can choose — and the
    choice is 404. A 204 for an id the console never held would tell the SPA its
    dropdown is now in a state it is not, and the operator's real question after a
    failed remove ("is that row still there?") would go unanswered.
    """
    was_selected = selection.current_id() == project_id
    if not registry.remove_project(project_id):
        raise ProjectNotRegistered(project_id)
    return was_selected


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write_token)],
    openapi_extra=_WRITE_TOKEN_SECURITY,
)
async def add_project(
    payload: AddProjectRequest,
    adapter: FileAdapter = Depends(get_file_adapter),
    registry: ProjectRegistry | None = Depends(get_project_registry),
) -> RegisteredProjectOut:
    """Register ``payload.path`` and return the switcher row it becomes.

    ``201`` with the created row rather than a bodiless ``201 + Location``: the SPA
    inserts the new row into an already-rendered dropdown, and the row carries three
    facts only the server holds (the minted id, the ``addedAt`` stamp and the probed
    ``condition``), so a client that got a URL back would immediately have to GET it.

    **NOT idempotent, by design.** A second POST of the same directory is
    ``409 duplicate_project_path`` carrying the existing row's id, never a silent
    no-op — so the SPA can say "you already track this" and offer to switch to it,
    which a 200-with-the-old-row could not be distinguished from a fresh add.

    Adding does NOT select. Registration and selection are separate acts, and conflating
    them would yank the board out from under an operator adding a second project while
    reading the first; the returned row therefore always reports ``selected: false``.

    The whole body — canonicalise, discover, insert, probe — runs in ONE
    ``anyio.to_thread.run_sync`` hop through
    :func:`~factory_console.api.deps._read_registry` (see :func:`_register_project` for
    the order and the errors it lets through), so a store the console cannot reach
    answers the same ``registry_unreadable`` 503 the listing does.

    Note that discovery WALKS UP from the given path, so registering a subdirectory of
    an App Factory project passes validation. That is deliberate and matches the CLI:
    the row records the path the user asked for, and the reads resolve the project from
    it the same way every other entry point does.
    """
    store = _require_registry(registry)
    return await _read_registry(
        partial(
            _register_project,
            payload.path,
            payload.name,
            adapter,
            store,
            RealProjectConditionProbe(),
        )
    )


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_write_token)],
    openapi_extra=_WRITE_TOKEN_SECURITY,
)
async def remove_project(
    project_id: ProjectIdPath,
    registry: ProjectRegistry | None = Depends(get_project_registry),
    selection: SelectionState = Depends(get_selection_state),
) -> Response:
    """Stop tracking ``project_id``. ``204``, and nothing on the project's disk changes.

    Removal is a CONSOLE-state operation: it deletes one row from the console's own
    table and never touches the project directory, which is why an empty ``204`` is the
    whole answer — there is no diff to preview and no artefact to report, unlike the
    ticket writes.

    Three answers other than success, each named:

    * the reserved ``session`` id → :class:`SessionProjectNotRemovable` (409). It is
      published as a row but was never registered, so there is nothing to delete.
    * an id no row answers to → :class:`ProjectNotRegistered` (404), decided in
      :func:`_remove_registered_project` from the port's ``False``.
    * an id that is neither 32 hex digits nor the sentinel → ``422`` at the boundary,
      from :data:`ProjectIdPath`, so a malformed id never reaches the store.

    **Removing the selected project clears the selection twice over, and both are
    needed.** The schema's ``ON DELETE SET NULL`` clears the PERSISTED selection as part
    of the delete; this handler additionally calls ``selection.select(None)`` to clear
    the PROCESS-LOCAL one, which is what fires the on-change hook so the watcher
    supervisor releases the watcher rooted at the directory that is no longer tracked.
    Without it the in-memory selection would outlive its row and every project-scoped
    read would answer ``selected_project_not_registered`` instead of the
    ``no_project_selected`` that is actually true.

    That clear is conditional — only when the removed row WAS the selection — because
    ``select(None)`` is not free: it bumps the watcher generation and tears down live
    updates for every SSE client, which removing some unrelated row must not do.

    It also runs on the EVENT-LOOP thread, outside the offloaded hop, unlike every other
    blocking step here. The hook it fires branches on whether a loop is running; on the
    loop it defers the watcher swap to a task and returns at once, while on a worker
    thread it would run the swap inline and lose the loop the new watcher needs. What
    stays on the loop is one ``UPDATE`` of a single row — and one already made redundant
    by the cascade, so it is the hook that is being bought, not the write.
    """
    if project_id == SESSION_PROJECT_ID:
        raise SessionProjectNotRemovable()
    store = _require_registry(registry)
    was_selected = await _read_registry(
        partial(_remove_registered_project, project_id, store, selection)
    )
    if was_selected:
        _guard_registry_io(partial(selection.select, None))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/projects/current",
    dependencies=[Depends(require_write_token)],
    openapi_extra=_WRITE_TOKEN_SECURITY,
)
async def select_current(
    payload: SelectProjectRequest,
    registry: ProjectRegistry | None = Depends(get_project_registry),
    selection: SelectionState = Depends(get_selection_state),
) -> CurrentSelectionResponse:
    """Point the console at ``payload.projectId`` and report what it now serves.

    Answers the SAME :class:`CurrentSelectionResponse` as ``GET /projects/current``, and
    builds it from the SAME :func:`_resolve_current` — called after the switch, so it
    describes the new selection. One shape for "what is selected" whether you asked or
    changed it, which is what lets the SPA feed a switch's response straight into the
    header it would otherwise refetch.

    **A degraded condition is NOT a precondition for selecting.** No servability probe
    runs before the switch: selecting a project whose directory has been deleted must
    SUCCEED, because that is precisely the state an operator selects into in order to
    then remove the row. The consequence is reported rather than refused — the response
    (and every subsequent read) names ``selected_project_missing`` /
    ``selected_project_unreadable`` — instead of the switch failing opaquely and leaving
    the operator pointed at a project they were trying to leave.

    Two refusals, both ``404 project_not_registered``:

    * an id no registry row answers to, raised by the registry underneath
      :meth:`~factory_console.services.project_selection.SelectionState.select` — the
      one write the port makes fail loudly rather than succeed at pointing the console
      at nothing.
    * the reserved ``session`` id when NO root is pinned. ``select()`` never persists
      that id, so it would otherwise be accepted and move the session to a sentinel that
      names no directory — the same "succeeded at selecting nothing" outcome, reached by
      the one path the registry cannot see. From the caller's side the two are the same
      statement (this id names nothing this console can serve), so they get the same
      code rather than a second one the SPA would have to learn. With a pin present the
      sentinel is a perfectly ordinary target, and is the ONLY selectable target in
      pinned mode — which is why it does not require a registry at all.

    ``select()`` runs on the EVENT-LOOP thread rather than in a worker: its on-change
    hook rebuilds the file watcher for the new root, and a ``RealFileWatcher`` captures
    the running loop when it starts, so off the loop every switch would silently degrade
    to watcher-less. The hook returns immediately (the swap is deferred to a task), so
    what actually runs on the loop is one small ``UPDATE``;
    :func:`~factory_console.api.deps._guard_registry_io` still names a store failure as
    the ``registry_unreadable`` 503. The resolution that follows is the ordinary single
    offloaded hop.
    """
    if payload.projectId == SESSION_PROJECT_ID:
        if selection.pinned_root is None:
            raise ProjectNotRegistered(payload.projectId)
    else:
        _require_registry(registry)
    _guard_registry_io(partial(selection.select, payload.projectId))
    resolved = await _read_registry(
        partial(_resolve_current, selection, registry, RealProjectConditionProbe())
    )
    return _current_response(resolved)
