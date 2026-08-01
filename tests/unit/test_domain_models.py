"""Unit tests for the shared Pydantic v2 domain models.

These pin the stable type surface every other track depends on: ticket-id
validation (:data:`TICKET_ID_PATTERN`), ``frozen`` / ``extra='forbid'`` config,
``model_dump`` round-trips, ``raw`` pass-through, and the :class:`RunState`
member/value contract. Deterministic and I/O-free — pydantic + stdlib only.
"""

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from factory_console.domain import (
    TICKET_ID_PATTERN,
    DepNeighborhood,
    Project,
    Roadmap,
    RunState,
    RunStateSource,
    Ticket,
    TicketId,
    TicketSummary,
)


class _TicketIdModel(BaseModel):
    """Minimal model that isolates :data:`TicketId` validation for id tests."""

    id: TicketId


def _make_project() -> Project:
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/docs/planning/tickets.json"),
        ticketsDir=Path("/proj/docs/planning/tickets"),
        roadmapPath=Path("/proj/ROADMAP.md"),
        runStateDir=Path("/proj/.factory/run-state"),
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _make_ticket() -> Ticket:
    return Ticket(
        id="CAD-118",
        title="Wire the file adapter",
        status="open",
        track="file-adapter",
        milestone="MVP",
        dependsOn=["CAD-100"],
        provides=["file-adapter port"],
        files=["server/factory_console/file_adapter/ticket_md.py"],
        filePath=Path("/proj/docs/planning/tickets/CAD-118.md"),
        bodyMarkdown="# Body",
        bodyHtml="<h1>Body</h1>",
        raw={"id": "CAD-118", "extraField": {"nested": [1, 2, 3]}},
    )


def _make_summary(ticket_id: str = "CAD-118") -> TicketSummary:
    return TicketSummary(
        id=ticket_id,
        title="Wire the file adapter",
        status="open",
        track="file-adapter",
        milestone="MVP",
        runState=RunState.ready,
        depCount=1,
        dependentCount=2,
    )


def _make_neighborhood() -> DepNeighborhood:
    return DepNeighborhood(
        ticket=_make_summary("CAD-118"),
        directDeps=[_make_summary("CAD-100")],
        directDependents=[_make_summary("CAD-152")],
        unresolvedDeps=["GHOST-1"],
    )


def _make_roadmap() -> Roadmap:
    return Roadmap(
        path=Path("/proj/ROADMAP.md"),
        bodyMarkdown="# Roadmap",
        bodyHtml="<h1>Roadmap</h1>",
    )


# --------------------------------------------------------------------------- #
# Ticket-id validation (path-traversal defense at the type layer)
# --------------------------------------------------------------------------- #


def test_ticket_id_pattern_is_the_documented_constant() -> None:
    # Downstream (file-adapter + backend) imports this verbatim, so it is pinned.
    assert TICKET_ID_PATTERN == r"^[A-Za-z0-9_.-]+$", (
        "TICKET_ID_PATTERN must not drift — it is the single source of truth "
        "imported verbatim by the file-adapter and backend"
    )


@pytest.mark.parametrize("valid_id", ["CAD-118", "T07", "a.b_c-1"])
def test_valid_ticket_id_is_accepted(valid_id: str) -> None:
    assert _TicketIdModel(id=valid_id).id == valid_id
    # And the same id validates on the real Ticket model (fresh construction,
    # so the StringConstraints actually run — model_copy would bypass them).
    ticket = Ticket(
        id=valid_id,
        title="t",
        status="open",
        filePath=Path("/proj/docs/planning/tickets/x.md"),
        bodyMarkdown="",
        bodyHtml="",
        raw={},
    )
    assert ticket.id == valid_id


@pytest.mark.parametrize(
    "invalid_id",
    [
        pytest.param("a/b", id="forward-slash"),
        pytest.param("../secrets", id="dotdot-traversal-with-slash"),
        pytest.param("a\\b", id="back-slash"),
        pytest.param("a b", id="space"),
        pytest.param("a\nb", id="newline"),
        pytest.param("", id="empty"),
    ],
)
def test_invalid_ticket_id_is_rejected(invalid_id: str) -> None:
    with pytest.raises(ValidationError):
        _TicketIdModel(id=invalid_id)


def test_ticket_model_rejects_invalid_id() -> None:
    # The constraint is enforced on the real Ticket model, not only the probe.
    with pytest.raises(ValidationError):
        Ticket(
            id="bad/id",
            title="t",
            status="open",
            filePath=Path("/proj/docs/planning/tickets/x.md"),
            bodyMarkdown="",
            bodyHtml="",
            raw={},
        )


def test_pattern_accepts_bare_dots_because_traversal_is_guarded_downstream() -> None:
    # '.' is an allowed character, so the regex alone does NOT reject a bare '.'
    # or '..'. The dot-dot traversal defense is defense-in-depth in the
    # file-adapter's _safe_resolve (containment check), NOT this pattern. Pin the
    # real behavior so downstream that imports the constant verbatim is not
    # misled into treating the regex as a complete traversal guard.
    for accepted in (".", ".."):
        assert _TicketIdModel(id=accepted).id == accepted


# --------------------------------------------------------------------------- #
# model_dump round-trips
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model",
    [
        _make_project(),
        _make_ticket(),
        _make_summary(),
        _make_neighborhood(),
        _make_roadmap(),
    ],
    ids=["Project", "Ticket", "TicketSummary", "DepNeighborhood", "Roadmap"],
)
def test_model_dump_round_trips(model: BaseModel) -> None:
    dumped = model.model_dump()
    rebuilt = type(model)(**dumped)
    assert rebuilt == model, f"{type(model).__name__} did not survive dump/rebuild"


# --------------------------------------------------------------------------- #
# frozen=True and extra='forbid'
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model, field, value",
    [
        (_make_project(), "rootPath", Path("/other")),
        (_make_ticket(), "title", "changed"),
        (_make_summary(), "status", "closed"),
        (_make_neighborhood(), "unresolvedDeps", []),
        (_make_roadmap(), "bodyHtml", "<p>changed</p>"),
    ],
    ids=["Project", "Ticket", "TicketSummary", "DepNeighborhood", "Roadmap"],
)
def test_frozen_blocks_attribute_assignment(model: BaseModel, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        setattr(model, field, value)


def test_extra_forbid_rejects_unknown_field_on_project() -> None:
    with pytest.raises(ValidationError):
        Project(
            rootPath=Path("/proj"),
            ticketsManifestPath=Path("/proj/tickets.json"),
            ticketsDir=Path("/proj/tickets"),
            discoveredAt=datetime(2026, 7, 20),
            somethingUnknown="x",
        )


def test_extra_forbid_rejects_unknown_field_on_ticket() -> None:
    with pytest.raises(ValidationError):
        Ticket(
            id="T1",
            title="t",
            status="open",
            filePath=Path("/proj/docs/planning/tickets/T1.md"),
            bodyMarkdown="",
            bodyHtml="",
            raw={},
            bogusField=1,
        )


# --------------------------------------------------------------------------- #
# Sensible defaults for optional/collection fields
# --------------------------------------------------------------------------- #


def test_ticket_optional_and_collection_fields_default_sensibly() -> None:
    ticket = Ticket(
        id="T1",
        title="t",
        status="open",
        filePath=Path("/proj/docs/planning/tickets/T1.md"),
        bodyMarkdown="",
        bodyHtml="",
        raw={},
    )
    assert ticket.track is None
    assert ticket.milestone is None
    assert ticket.dependsOn == []
    assert ticket.provides == []
    assert ticket.files == []


def test_project_optional_paths_default_to_none() -> None:
    project = Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/tickets.json"),
        ticketsDir=Path("/proj/tickets"),
        discoveredAt=datetime(2026, 7, 20),
    )
    assert project.roadmapPath is None
    assert project.runStateDir is None
    assert project.runStateSource is None


# --------------------------------------------------------------------------- #
# Project.runStateDir <-> runStateSource reconciliation
# --------------------------------------------------------------------------- #
#
# The two fields describe ONE fact, and different call sites read different ones
# (the write gate reads the source; the writer's forbidden-path guard also
# consults the directory). A Project that stored a contradiction would let them
# authorize against two different artifacts, so the model reconciles both
# directions and refuses what it cannot reconcile.


def _project(**overrides: object) -> Project:
    """Build a Project rooted at ``/proj``, overriding the run-state fields."""
    return Project(
        rootPath=Path("/proj"),
        ticketsManifestPath=Path("/proj/tickets.json"),
        ticketsDir=Path("/proj/tickets"),
        discoveredAt=datetime(2026, 7, 20),
        **overrides,  # type: ignore[arg-type]
    )


def test_run_state_dir_alone_derives_the_directory_source() -> None:
    project = _project(runStateDir=Path("/proj/.factory/run-state"))

    assert project.runStateSource == RunStateSource(
        kind="directory", path=Path("/proj/.factory/run-state")
    )


def test_directory_source_alone_fills_in_the_run_state_dir() -> None:
    """The reverse derivation: adopting the new field must not drop the old one."""
    project = _project(
        runStateSource=RunStateSource(kind="directory", path=Path("/proj/.factory/run-state"))
    )

    assert project.runStateDir == Path("/proj/.factory/run-state")


def test_json_source_leaves_the_run_state_dir_none() -> None:
    """A JSON source has no marker directory — ``runStateDir``'s documented meaning."""
    project = _project(
        runStateSource=RunStateSource(kind="json", path=Path("/proj/.factory/run-state.json"))
    )

    assert project.runStateDir is None


def test_json_source_alongside_a_run_state_dir_is_refused() -> None:
    with pytest.raises(ValidationError):
        _project(
            runStateDir=Path("/proj/.factory/run-state"),
            runStateSource=RunStateSource(kind="json", path=Path("/proj/.factory/run-state.json")),
        )


def test_directory_source_disagreeing_with_the_run_state_dir_is_refused() -> None:
    with pytest.raises(ValidationError):
        _project(
            runStateDir=Path("/proj/.factory/run-state"),
            runStateSource=RunStateSource(kind="directory", path=Path("/proj/docs/.run-state")),
        )


def test_directory_source_agreeing_with_the_run_state_dir_is_accepted() -> None:
    shared = Path("/proj/.factory/run-state")
    project = _project(
        runStateDir=shared, runStateSource=RunStateSource(kind="directory", path=shared)
    )

    assert project.runStateDir == shared
    assert project.runStateSource is not None
    assert project.runStateSource.path == shared


# ``runStateSource`` arrives as a MAPPING on every deserialization path — a
# ``model_dump()`` round-trip, ``model_validate``, the REST layer. Reconciling
# only the already-constructed instance form would skip these checks for exactly
# the over-the-wire path they matter most on, so they are pinned in both shapes.


def test_mapping_form_directory_source_also_fills_in_the_run_state_dir() -> None:
    project = _project(runStateSource={"kind": "directory", "path": "/proj/.factory/run-state"})

    assert project.runStateDir == Path("/proj/.factory/run-state")


def test_mapping_form_json_source_alongside_a_run_state_dir_is_refused() -> None:
    with pytest.raises(ValidationError):
        _project(
            runStateDir=Path("/proj/.factory/run-state"),
            runStateSource={"kind": "json", "path": "/proj/.factory/run-state.json"},
        )


def test_mapping_form_directory_source_disagreeing_is_refused() -> None:
    with pytest.raises(ValidationError):
        _project(
            runStateDir=Path("/proj/.factory/run-state"),
            runStateSource={"kind": "directory", "path": "/proj/docs/.run-state"},
        )


def test_an_invalid_source_is_left_for_pydantic_to_report_on_its_own_field() -> None:
    """A junk source must surface as a field error, not a reconciliation crash."""
    with pytest.raises(ValidationError) as exc_info:
        _project(runStateSource={"kind": "not-a-kind", "path": "/proj/x"})

    assert "runStateSource" in str(exc_info.value)


def test_a_project_survives_a_model_dump_round_trip_with_a_json_source() -> None:
    """The round trip re-enters the validator in mapping form — it must not be refused."""
    original = _project(
        runStateSource=RunStateSource(kind="json", path=Path("/proj/.factory/run-state.json"))
    )

    assert Project(**original.model_dump()) == original


# --------------------------------------------------------------------------- #
# Ticket.raw pass-through
# --------------------------------------------------------------------------- #


def test_ticket_raw_preserves_arbitrary_nested_content() -> None:
    raw = {
        "id": "CAD-118",
        "schemaVersion": 3,
        "unknownKey": {"deep": [1, {"flag": True}, None]},
    }
    ticket = Ticket(
        id="CAD-118",
        title="t",
        status="open",
        filePath=Path("/proj/docs/planning/tickets/CAD-118.md"),
        bodyMarkdown="",
        bodyHtml="",
        raw=raw,
    )
    assert ticket.raw == raw
    assert ticket.raw["unknownKey"]["deep"][1]["flag"] is True


# --------------------------------------------------------------------------- #
# RunState member + value contract (pinned so it cannot drift)
# --------------------------------------------------------------------------- #


def test_run_state_members_are_exactly_these() -> None:
    assert [member.name for member in RunState] == [
        "todo",
        "in_flight",
        "ready",
        "merged",
        "in_progress",
        "in_part",
        "in_submilestone",
        "flagged",
        "failed",
        "needs_human",
        "unknown",
        "absent",
    ]


def test_run_state_values_mirror_the_name_their_source_uses() -> None:
    # Named by the legacy run-state DIRECTORY form (``in-flight`` hyphenated).
    assert RunState.todo.value == "todo"
    assert RunState.in_flight.value == "in-flight"
    assert RunState.ready.value == "ready"
    assert RunState.merged.value == "merged"
    # Named by the factory's run-state.json (FAC_STATES, underscored). These are
    # NOT hyphenated variants of the directory names: the factory has no
    # ``in-flight`` at all, and ``in_progress`` must never collapse onto it.
    assert RunState.in_progress.value == "in_progress"
    assert RunState.in_part.value == "in_part"
    assert RunState.in_submilestone.value == "in_submilestone"
    assert RunState.flagged.value == "flagged"
    assert RunState.failed.value == "failed"
    assert RunState.needs_human.value == "needs_human"
    # Named by no source: no run-state source present, or it could not be read.
    assert RunState.unknown.value == "unknown"
    # Named by no source either — a source resolved and simply does not list
    # this ticket (T80).
    assert RunState.absent.value == "absent"


def test_run_state_is_a_str_subclass() -> None:
    assert issubclass(RunState, str)
    assert RunState.todo == "todo"
    assert RunState.in_flight == "in-flight"


# --------------------------------------------------------------------------- #
# frozen hashability behavior (confirming "hashable/immutable as expected")
# --------------------------------------------------------------------------- #


def test_frozen_models_with_hashable_fields_are_hashable() -> None:
    # frozen=True makes a model hashable when every field value is hashable.
    assert isinstance(hash(_make_project()), int)
    assert isinstance(hash(_make_summary()), int)


def test_frozen_models_with_collection_fields_are_unhashable() -> None:
    # Ticket / DepNeighborhood / Roadmap carry list/dict fields (Roadmap now
    # holds milestones[]), so hashing raises even though the models are frozen —
    # attribute assignment is still blocked.
    with pytest.raises(TypeError):
        hash(_make_ticket())
    with pytest.raises(TypeError):
        hash(_make_neighborhood())
    with pytest.raises(TypeError):
        hash(_make_roadmap())
