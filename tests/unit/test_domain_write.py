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


CONTENT = {
    "context": "Why this ticket exists.",
    "approach": "1. Do it.",
    "criticalFiles": ["server/factory_console/domain/write.py"],
    "interfaceData": "N/A",
    "verificationCommands": ["pytest tests/unit/test_domain_write.py -q"],
}
"""The five CONTENT fields, valid. Spread into every draft/edit built here.

Named once because every constructor below needs all five — v3 requires them — and
five keyword arguments repeated per case is how a fixture comes to differ from its
siblings in a way no test is about.
"""


def _make_draft(ticket_id: str = "T55") -> TicketDraft:
    return TicketDraft(
        id=ticket_id,
        title="Write-path domain models",
        track="file-adapter",
        milestone="v2",
        dependsOn=["T07"],
        provides="domain/write.py",
        **CONTENT,
    )


def _make_edit() -> TicketEdit:
    return TicketEdit(
        title="Write-path domain models",
        track="file-adapter",
        milestone="v2",
        dependsOn=["T07"],
        provides="domain/write.py",
        **CONTENT,
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
        TicketDraft(id="T55", **CONTENT)


@pytest.mark.parametrize("missing", sorted(CONTENT))
def test_every_content_field_is_required(missing: str) -> None:
    # v3's schema makes all five required, and the DTO enforces it HERE so a bad
    # request is a 422 naming the field rather than a 500 from the console's own
    # validation of text it just built.
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", title="t", **{k: v for k, v in CONTENT.items() if k != missing})


@pytest.mark.parametrize("field", ["criticalFiles", "verificationCommands"])
def test_the_two_list_fields_reject_an_empty_list(field: str) -> None:
    # The schema's ``minItems: 1``, and it states the reasons: an empty
    # ``critical_files`` silently weakens the overlap filter that keeps two lanes off
    # one path, and under INV-42 a ticket declaring no verification command can never
    # be verified, only assumed. An empty list is the shape that passes "did you send
    # the field?" while answering nothing.
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", title="t", **{**CONTENT, field: []})


@pytest.mark.parametrize("field", ["context", "approach", "interfaceData"])
def test_the_prose_fields_reject_an_empty_string(field: str) -> None:
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", title="t", **{**CONTENT, field: ""})


def test_ticket_edit_requires_the_content_fields() -> None:
    with pytest.raises(ValidationError):
        TicketEdit(title="t")


@pytest.mark.parametrize("retired", ["bodyMarkdown", "frontMatter", "files"])
def test_the_retired_write_fields_are_rejected_not_ignored(retired: str) -> None:
    # ``extra="forbid"`` is what makes the surface change VISIBLE. A client still
    # sending a Markdown body gets a 422 naming the field, instead of a silent accept
    # that writes a ticket with the body dropped — the failure mode this whole PR
    # exists to avoid one level up.
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", title="t", **CONTENT, **{retired: "x"})


# --------------------------------------------------------------------------- #
# extra='forbid'
# --------------------------------------------------------------------------- #


def test_ticket_draft_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TicketDraft(id="T55", title="t", **CONTENT, bogusField=1)


def test_ticket_edit_forbids_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TicketEdit(title="t", **CONTENT, bogusField=1)


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
    draft = TicketDraft(id="T55", title="t", **CONTENT)
    assert draft.track is None
    assert draft.milestone is None
    assert draft.dependsOn == []
    assert draft.provides == ""
    # The one content field the schema makes optional, and the only one that may
    # default: absent notes is a planner who did not answer, which is a different
    # document from one who answered with nothing.
    assert draft.verificationNotes is None


def test_ticket_edit_optional_and_collection_fields_default_sensibly() -> None:
    edit = TicketEdit(title="t", **CONTENT)
    assert edit.track is None
    assert edit.milestone is None
    assert edit.dependsOn == []
    assert edit.provides == ""
    assert edit.verificationNotes is None


def test_diff_preview_files_default_empty() -> None:
    assert DiffPreview(ticketId="T55").files == []


def test_write_result_changed_files_default_empty_and_ticket_none() -> None:
    result = WriteResult(applied=False, ticketId="T55", diff=_make_diff_preview())
    assert result.changedFiles == []
    assert result.ticket is None


# --------------------------------------------------------------------------- #
# WriteResult applied <=> ticket invariant
# --------------------------------------------------------------------------- #


def test_write_result_rejects_applied_true_without_ticket() -> None:
    with pytest.raises(ValidationError):
        WriteResult(applied=True, ticketId="T55", diff=_make_diff_preview(), ticket=None)


def test_write_result_rejects_applied_false_with_ticket() -> None:
    with pytest.raises(ValidationError):
        WriteResult(applied=False, ticketId="T55", diff=_make_diff_preview(), ticket=_make_ticket())
