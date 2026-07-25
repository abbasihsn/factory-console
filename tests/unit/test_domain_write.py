"""Unit tests for the write-path Pydantic v2 domain models.

These pin the write DTO surface the file-adapter track and the API depend on:
ticket-id validation reused from :data:`TICKET_ID_PATTERN`, required-field and
``extra='forbid'`` enforcement, ``frozen`` immutability, camelCase JSON
serialization for the outbound envelope, the :class:`FileDiff` ``changeKind``
Literal, ``model_dump`` round-trips, and sensible collection defaults.
Deterministic and I/O-free — pydantic + stdlib only.
"""

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from factory_console.domain import Ticket
from factory_console.domain.write import (
    DiffPreview,
    FileDiff,
    TicketDraft,
    TicketEdit,
    WriteResult,
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


def _make_draft(ticket_id: str = "T55") -> TicketDraft:
    return TicketDraft(
        id=ticket_id,
        title="Write-path domain models",
        track="file-adapter",
        milestone="v2",
        dependsOn=["T07"],
        provides="domain/write.py",
        files=["server/factory_console/domain/write.py"],
        bodyMarkdown="# Body",
        frontMatter={"status": "todo"},
    )


def _make_edit() -> TicketEdit:
    return TicketEdit(
        title="Write-path domain models",
        track="file-adapter",
        milestone="v2",
        dependsOn=["T07"],
        provides="domain/write.py",
        files=["server/factory_console/domain/write.py"],
        bodyMarkdown="# Body",
        frontMatter={"status": "todo"},
    )


def _make_file_diff() -> FileDiff:
    return FileDiff(
        path="docs/planning/tickets/T55.md",
        changeKind="create",
        diff="--- /dev/null\n+++ b/docs/planning/tickets/T55.md\n@@\n+# T55\n",
    )


def _make_diff_preview() -> DiffPreview:
    return DiffPreview(ticketId="T55", files=[_make_file_diff()])


# --------------------------------------------------------------------------- #
# Ticket-id validation on TicketDraft (reused TICKET_ID_PATTERN)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("valid_id", ["T55", "CAD-118", "a.b_c-1"])
def test_ticket_draft_accepts_valid_id(valid_id: str) -> None:
    assert _make_draft(valid_id).id == valid_id


@pytest.mark.parametrize(
    "invalid_id",
    [
        pytest.param("a/b", id="forward-slash"),
        pytest.param("../secrets", id="dotdot-traversal-with-slash"),
        pytest.param("a\\b", id="back-slash"),
        pytest.param("a b", id="space"),
        pytest.param("", id="empty"),
    ],
)
def test_ticket_draft_rejects_invalid_id(invalid_id: str) -> None:
    with pytest.raises(ValidationError):
        _make_draft(invalid_id)


# --------------------------------------------------------------------------- #
# Missing required fields raise
# --------------------------------------------------------------------------- #


def test_ticket_draft_requires_title() -> None:
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", bodyMarkdown="# Body")


def test_ticket_draft_requires_body_markdown() -> None:
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", title="t")


def test_ticket_edit_requires_body_markdown() -> None:
    with pytest.raises(ValidationError):
        TicketEdit(title="t")


# --------------------------------------------------------------------------- #
# extra='forbid'
# --------------------------------------------------------------------------- #


def test_ticket_draft_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", title="t", bodyMarkdown="# Body", bogusField=1)


def test_ticket_edit_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TicketEdit(title="t", bodyMarkdown="# Body", bogusField=1)


def test_file_diff_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        FileDiff(path="a", changeKind="create", diff="", bogusField=1)


def test_diff_preview_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        DiffPreview(ticketId="T55", bogusField=1)


def test_write_result_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        WriteResult(
            applied=False,
            ticketId="T55",
            diff=_make_diff_preview(),
            bogusField=1,
        )


# --------------------------------------------------------------------------- #
# frozen=True blocks attribute assignment
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model, field, value",
    [
        (_make_draft(), "title", "changed"),
        (_make_edit(), "title", "changed"),
        (_make_file_diff(), "path", "other"),
        (_make_diff_preview(), "ticketId", "T99"),
        (
            WriteResult(applied=False, ticketId="T55", diff=_make_diff_preview()),
            "applied",
            True,
        ),
    ],
    ids=["TicketDraft", "TicketEdit", "FileDiff", "DiffPreview", "WriteResult"],
)
def test_frozen_blocks_attribute_assignment(model: BaseModel, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        setattr(model, field, value)


# --------------------------------------------------------------------------- #
# FileDiff.changeKind Literal contract
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["create", "modify", "delete"])
def test_file_diff_accepts_valid_change_kind(kind: str) -> None:
    assert FileDiff(path="a", changeKind=kind, diff="").changeKind == kind


def test_file_diff_rejects_invalid_change_kind() -> None:
    with pytest.raises(ValidationError):
        FileDiff(path="a", changeKind="rename", diff="")


# --------------------------------------------------------------------------- #
# camelCase JSON serialization of the outbound envelope
# --------------------------------------------------------------------------- #


def test_file_diff_serializes_to_camel_case() -> None:
    dumped = _make_file_diff().model_dump(mode="json")
    assert set(dumped) == {"path", "changeKind", "diff"}


def test_diff_preview_serializes_to_camel_case() -> None:
    dumped = _make_diff_preview().model_dump(mode="json")
    assert set(dumped) == {"ticketId", "files"}
    assert set(dumped["files"][0]) == {"path", "changeKind", "diff"}


def test_write_result_apply_shape_serializes_to_camel_case() -> None:
    result = WriteResult(
        applied=True,
        ticketId="CAD-118",
        changedFiles=["docs/planning/tickets/CAD-118.md"],
        diff=_make_diff_preview(),
        ticket=_make_ticket(),
    )
    dumped = result.model_dump(mode="json")
    assert set(dumped) == {"applied", "ticketId", "changedFiles", "diff", "ticket"}
    assert dumped["applied"] is True
    assert dumped["changedFiles"] == ["docs/planning/tickets/CAD-118.md"]
    assert dumped["diff"]["ticketId"] == "T55"
    assert dumped["ticket"]["id"] == "CAD-118"


def test_write_result_dry_run_shape_serializes_to_camel_case() -> None:
    result = WriteResult(
        applied=False,
        ticketId="T55",
        changedFiles=["docs/planning/tickets/T55.md"],
        diff=_make_diff_preview(),
        ticket=None,
    )
    dumped = result.model_dump(mode="json")
    assert set(dumped) == {"applied", "ticketId", "changedFiles", "diff", "ticket"}
    assert dumped["applied"] is False
    assert dumped["ticket"] is None
    assert dumped["changedFiles"] == ["docs/planning/tickets/T55.md"]


# --------------------------------------------------------------------------- #
# model_dump round-trips
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "model",
    [
        _make_draft(),
        _make_edit(),
        _make_file_diff(),
        _make_diff_preview(),
        WriteResult(
            applied=True,
            ticketId="CAD-118",
            changedFiles=["docs/planning/tickets/CAD-118.md"],
            diff=_make_diff_preview(),
            ticket=_make_ticket(),
        ),
    ],
    ids=["TicketDraft", "TicketEdit", "FileDiff", "DiffPreview", "WriteResult"],
)
def test_model_dump_round_trips(model: BaseModel) -> None:
    dumped = model.model_dump()
    rebuilt = type(model)(**dumped)
    assert rebuilt == model, f"{type(model).__name__} did not survive dump/rebuild"


# --------------------------------------------------------------------------- #
# Sensible defaults for optional/collection fields
# --------------------------------------------------------------------------- #


def test_ticket_draft_optional_and_collection_fields_default_sensibly() -> None:
    draft = TicketDraft(id="T55", title="t", bodyMarkdown="# Body")
    assert draft.track is None
    assert draft.milestone is None
    assert draft.dependsOn == []
    assert draft.provides == ""
    assert draft.files == []
    assert draft.frontMatter == {}


def test_ticket_edit_optional_and_collection_fields_default_sensibly() -> None:
    edit = TicketEdit(title="t", bodyMarkdown="# Body")
    assert edit.track is None
    assert edit.milestone is None
    assert edit.dependsOn == []
    assert edit.provides == ""
    assert edit.files == []
    assert edit.frontMatter == {}


def test_diff_preview_files_default_empty() -> None:
    assert DiffPreview(ticketId="T55").files == []


def test_write_result_changed_files_default_empty_and_ticket_none() -> None:
    result = WriteResult(applied=False, ticketId="T55", diff=_make_diff_preview())
    assert result.changedFiles == []
    assert result.ticket is None
