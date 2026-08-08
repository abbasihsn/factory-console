"""Unit tests for reading an App Factory **v3** JSON ticket.

The JSON half of the contract ``test_ticket_md.py`` covers for Markdown, entered the
same way — through :func:`~factory_console.file_adapter.ticket_content.read_ticket_body`,
the composed path a request actually takes — plus the two things only this format has:
schema validation that fails LOUDLY, and a rendering that must match the factory's own.

The rendering assertions are the load-bearing ones. ``factory-ticket render`` emits this
same document for a lane to build from; if the two disagree, the ticket a human reviews in
the console is not the ticket a lane implements. They are pinned here structurally, and
compared byte-for-byte against the real binary by
``tests/integration/test_cross_repo_contract.py`` wherever App Factory is on disk.

All I/O is confined to ``tmp_path`` except the fixture-project tests at the end, which
read ``tests/fixtures/projects/factory_v3/`` — the layout ``factory-ticket migrate``
actually produces.
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from factory_console.domain import Project
from factory_console.domain.ticket import Ticket
from factory_console.errors import to_error_response
from factory_console.file_adapter.manifest import iter_ticket_stubs
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.ticket_content import (
    TicketFormatUnsupported,
    enrich_ticket,
    read_ticket_body,
)
from factory_console.file_adapter.ticket_json import (
    TicketInvalid,
    parse_ticket_content,
    render_ticket_markdown,
)
from factory_console.file_adapter.ticket_md import TicketFileMissing, TicketFileUnreadable

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "factory_v3"

VALID = {
    "id": "T09",
    "context": "Why this exists.",
    "approach": "1. Do the thing.\n2. Then the other.",
    "critical_files": ["src/auth/routes.py", "src/auth/models.py"],
    "interface_data": "POST /auth/token -> {token, expiresAt}.",
    "verification": {"commands": ["pytest tests/auth -q"], "notes": "needs DATABASE_URL"},
}
"""A ticket satisfying every clause of ``schemas/ticket.schema.json``."""


def _make_project(tmp_path: Path) -> Project:
    """A Project rooted at ``tmp_path/project`` with a real tickets dir."""
    root = tmp_path / "project"
    tickets_dir = root / "docs" / "planning" / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=tickets_dir,
        discoveredAt=datetime(2026, 8, 8, 12, 0, 0),
    )


def _write(project: Project, ticket_id: str, payload: object) -> Path:
    """Write ``payload`` as ``<ticketsDir>/<id>.json`` and return the path."""
    path = project.ticketsDir / f"{ticket_id}.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _read(project: Project, ticket_id: str, entry: dict | None = None):
    """Read ``<ticketsDir>/<id>.json`` through the format dispatcher."""
    return read_ticket_body(project, ticket_id, project.ticketsDir / f"{ticket_id}.json", entry)


# --------------------------------------------------------------------------- #
# The happy path: five sections, and critical_files answered
# --------------------------------------------------------------------------- #


def test_a_valid_ticket_renders_the_five_sections_in_order(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)

    body = _read(project, "T09", {"title": "Auth endpoint", "milestone": "v1.0"})

    headings = [line for line in body.markdown.splitlines() if line.startswith("## ")]
    assert headings == [
        "## Context",
        "## Staged approach",
        "## Critical files",
        "## Interface & data",
        "## Verification",
    ]


def test_critical_files_are_answered_by_the_content_file(tmp_path: Path) -> None:
    # The distinction TicketBody.content exists to carry: a JSON ticket ANSWERS the
    # question, a Markdown one does not (None). Without it the v3 index — which has no
    # `files` key at all — would leave every ticket showing an empty file list while the
    # real one sat one file away, unread.
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)

    body = _read(project, "T09")

    assert body.content is not None
    assert body.content.criticalFiles == ["src/auth/routes.py", "src/auth/models.py"]
    assert body.front_matter == {}, "a v3 ticket carries no front-matter"


def test_the_structured_content_survives_the_read_alongside_the_rendering(
    tmp_path: Path,
) -> None:
    # bodyMarkdown is a RENDERED view; an edit form cannot seed five fields from a
    # paragraph. Both come from one read of one file, so the page a human reviews and the
    # form they then edit cannot disagree about what the ticket says.
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)

    content = _read(project, "T09").content

    assert content is not None
    assert content.context == VALID["context"]
    assert content.approach == VALID["approach"]
    assert content.interfaceData == VALID["interface_data"]
    assert content.verificationCommands == VALID["verification"]["commands"]
    assert content.verificationNotes == VALID["verification"].get("notes")


def test_the_body_carries_the_prose_and_the_bulleted_lists(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)

    markdown = _read(project, "T09").markdown

    assert "Why this exists." in markdown
    assert "1. Do the thing." in markdown
    assert "- `src/auth/routes.py`" in markdown
    assert "- `pytest tests/auth -q`" in markdown
    assert "Notes: needs DATABASE_URL" in markdown


def test_absent_notes_emit_no_notes_line(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", {**VALID, "verification": {"commands": ["true"]}})

    assert "Notes:" not in _read(project, "T09").markdown


# --------------------------------------------------------------------------- #
# The heading comes from the INDEX, because that is where v3 keeps it
# --------------------------------------------------------------------------- #


def test_heading_and_metadata_come_from_the_manifest_entry(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)
    entry = {
        "id": "T09",
        "title": "Auth endpoint",
        "milestone": "v1.0",
        "track": "backend",
        "depends_on": ["T04", "T05"],
        "provides": "A token",
    }

    lines = _read(project, "T09", entry).markdown.splitlines()

    assert lines[0] == "# [T09] Auth endpoint"
    assert lines[1] == (
        "milestone: v1.0 · track: backend · depends_on: T04, T05 · provides: A token"
    )


def test_an_entry_with_nothing_to_say_renders_the_factory_placeholders(tmp_path: Path) -> None:
    # The `?` / `none` / `—` placeholders are the factory's, not a preference. Two
    # renderers that agree on everything but the empty cases still disagree, and the
    # empty case is exactly where a diff shows up.
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)

    lines = _read(project, "T09", {}).markdown.splitlines()

    assert lines[0] == "# [T09] (untitled)"
    assert lines[1] == "milestone: ? · track: ? · depends_on: none · provides: —"


def test_camelcase_dependson_is_read_like_the_manifest_reader_reads_it(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)

    lines = _read(project, "T09", {"dependsOn": ["T04"]}).markdown.splitlines()

    assert "depends_on: T04" in lines[1]


# --------------------------------------------------------------------------- #
# Invalid tickets fail LOUDLY — never an empty body
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mutation", "expected_in_reason"),
    [
        pytest.param({"critical_files": []}, "critical_files", id="empty-critical-files"),
        pytest.param({"context": ""}, "context", id="empty-context"),
        pytest.param({"verification": {"commands": []}}, "commands", id="no-commands"),
        pytest.param({"estimate": "3d"}, "estimate", id="unknown-key-forbidden"),
    ],
)
def test_a_schema_violation_raises_ticket_invalid(
    tmp_path: Path, mutation: dict, expected_in_reason: str
) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", {**VALID, **mutation})

    with pytest.raises(TicketInvalid) as caught:
        _read(project, "T09")

    assert expected_in_reason in caught.value.details["reason"]


@pytest.mark.parametrize("missing", sorted(VALID))
def test_every_required_field_is_required(tmp_path: Path, missing: str) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", {key: value for key, value in VALID.items() if key != missing})

    with pytest.raises(TicketInvalid):
        _read(project, "T09")


def test_malformed_json_raises_ticket_invalid_not_an_empty_body(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", "{ not json at all")

    with pytest.raises(TicketInvalid) as caught:
        _read(project, "T09")

    assert "not valid JSON" in caught.value.message


def test_a_json_array_at_top_level_raises_ticket_invalid(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _write(project, "T09", [VALID])

    with pytest.raises(TicketInvalid) as caught:
        _read(project, "T09")

    assert "top level is list" in caught.value.details["reason"]


def test_an_id_disagreeing_with_the_manifest_raises(tmp_path: Path) -> None:
    # The content file is reached THROUGH the index, so a mismatch means one of the two
    # was hand-edited. Rendering it anyway puts one ticket's prose under another's
    # heading — a wrong answer that looks like a right one.
    project = _make_project(tmp_path)
    _write(project, "T09", {**VALID, "id": "T99"})

    with pytest.raises(TicketInvalid) as caught:
        _read(project, "T09")

    assert "hand-edited" in caught.value.details["reason"]


def test_ticket_invalid_reason_never_echoes_the_offending_input(tmp_path: Path) -> None:
    # A ticket body runs to thousands of characters and reaches an HTTP envelope and a
    # log line from here. Pydantic's own report includes the input; this one must not.
    secret = "S3CRET-VALUE-THAT-MUST-NOT-LEAK"
    project = _make_project(tmp_path)
    _write(project, "T09", {**VALID, "context": secret, "estimate": secret})

    with pytest.raises(TicketInvalid) as caught:
        _read(project, "T09")

    assert secret not in json.dumps(to_error_response(caught.value))


# --------------------------------------------------------------------------- #
# Format dispatch
# --------------------------------------------------------------------------- #


def test_the_two_formats_dispatch_per_ticket_in_one_project(tmp_path: Path) -> None:
    # The state a repository is actually in mid-migration: the factory's own loader
    # accepts either for one release, so a project holding both must read both — per
    # ticket, decided by the manifest-declared path, not project-wide.
    project = _make_project(tmp_path)
    _write(project, "T09", VALID)
    (project.ticketsDir / "T10.md").write_text("---\ntitle: Old\n---\n\n# Body\n", "utf-8")

    json_body = read_ticket_body(project, "T09", project.ticketsDir / "T09.json")
    md_body = read_ticket_body(project, "T10", project.ticketsDir / "T10.md")

    assert "## Context" in json_body.markdown
    assert json_body.content is not None
    assert md_body.markdown == "\n# Body\n"
    assert md_body.content is None


def test_a_suffix_with_no_reader_is_refused_not_guessed_at(tmp_path: Path) -> None:
    # Falling back to Markdown would turn a malformed v3 ticket into a document whose
    # body is raw JSON — the silent degradation TicketInvalid exists to replace.
    project = _make_project(tmp_path)
    (project.ticketsDir / "T09.yaml").write_text("id: T09\n", encoding="utf-8")

    with pytest.raises(TicketFormatUnsupported) as caught:
        read_ticket_body(project, "T09", project.ticketsDir / "T09.yaml")

    assert caught.value.details["suffix"] == ".yaml"
    assert ".json" in caught.value.message and ".md" in caught.value.message


def test_suffix_dispatch_is_case_insensitive(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project.ticketsDir / "T09.JSON").write_text(json.dumps(VALID), encoding="utf-8")

    body = read_ticket_body(project, "T09", project.ticketsDir / "T09.JSON")

    assert "## Context" in body.markdown


# --------------------------------------------------------------------------- #
# File-level failures are shared with the Markdown reader, deliberately
# --------------------------------------------------------------------------- #


def test_a_missing_json_ticket_raises_the_shared_missing_error(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(TicketFileMissing):
        _read(project, "T404")


def test_non_utf8_bytes_raise_the_shared_unreadable_error(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project.ticketsDir / "Tbad.json").write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(TicketFileUnreadable):
        _read(project, "Tbad")


def test_a_directory_at_the_ticket_path_raises_unreadable(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    (project.ticketsDir / "Tdir.json").mkdir()
    with pytest.raises(TicketFileUnreadable):
        _read(project, "Tdir")


def test_an_unsafe_id_is_refused_before_any_read(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    with pytest.raises(PathTraversal):
        read_ticket_body(project, "../etc/passwd")


def test_a_declared_path_escaping_the_root_is_refused(tmp_path: Path) -> None:
    # A manifest `path` is repository data, not user input — but it is still data, and
    # refusing it less firmly because it arrived by a different route is a hole.
    project = _make_project(tmp_path)
    with pytest.raises(PathTraversal):
        read_ticket_body(project, "T09", Path("../../../etc/passwd"))


def test_error_contracts_and_no_path_leak() -> None:
    invalid = TicketInvalid("T09", "context: field required")
    unsupported = TicketFormatUnsupported("T09", ".yaml")

    assert (invalid.code, invalid.status) == ("ticket_invalid", 500)
    assert (unsupported.code, unsupported.status) == ("ticket_format_unsupported", 500)
    for exc in (invalid, unsupported):
        serialized = json.dumps(to_error_response(exc))
        assert "/tmp" not in serialized and "docs/planning" not in serialized


# --------------------------------------------------------------------------- #
# Against the fixture project — the layout `factory-ticket migrate` produces
# --------------------------------------------------------------------------- #


def _fixture_project() -> Project:
    return Project(
        rootPath=FIXTURE,
        ticketsManifestPath=FIXTURE / "docs" / "planning" / "tickets.json",
        ticketsDir=FIXTURE / "docs" / "planning" / "tickets",
        discoveredAt=datetime(2026, 8, 8, 12, 0, 0),
    )


def test_every_fixture_ticket_reads_and_renders() -> None:
    project = _fixture_project()
    stubs = list(iter_ticket_stubs(project))

    assert [stub.id for stub in stubs] == ["T01", "T02", "T03"]
    for stub in stubs:
        enriched = enrich_ticket(project, stub)
        assert enriched.bodyMarkdown.startswith(f"# [{stub.id}] ")
        assert "## Verification" in enriched.bodyMarkdown


def test_enrich_takes_files_from_critical_files_and_keeps_manifest_fields() -> None:
    project = _fixture_project()
    stub = next(stub for stub in iter_ticket_stubs(project) if stub.id == "T02")

    enriched = enrich_ticket(project, stub)

    # The v3 INDEX carries no `files` key, so this can only have come from the content
    # file — and it is the overlap the factory serializes two lanes on.
    assert enriched.files == ["src/foundation/entry.py", "src/capability/handler.py"]
    assert enriched.title == "Depends on T01, in the same sub-version"
    assert enriched.milestone == "v1.0"
    assert enriched.raw["frontMatter"] == {}
    assert enriched is not stub


def test_enrich_publishes_the_structured_content_and_files_agrees_with_it() -> None:
    # `files` is the format-agnostic DISPLAY projection and `content.criticalFiles` is the
    # editable field; they are assigned from ONE value so they cannot drift into
    # disagreeing about a list both of them show.
    project = _fixture_project()
    stub = next(stub for stub in iter_ticket_stubs(project) if stub.id == "T02")

    enriched = enrich_ticket(project, stub)

    assert enriched.content is not None
    assert enriched.content.criticalFiles == enriched.files
    # The rendered view is a DERIVATION of the structured source, not a second opinion.
    assert enriched.content.context in enriched.bodyMarkdown
    assert enriched.content.approach in enriched.bodyMarkdown


def test_enrich_publishes_no_content_for_a_markdown_ticket(tmp_path: Path) -> None:
    # The read-side twin of TicketFormatRetired: a ticket with no structured content is
    # exactly a ticket whose edit the write path refuses, so `content is None` is what
    # lets a client decline to open a form whose Save is guaranteed to 409. Its
    # manifest-declared `files` must survive — a format that never had the field must not
    # be able to erase one that answered.
    project = _make_project(tmp_path)
    (project.ticketsDir / "T10.md").write_text("# Old\n", "utf-8")
    stub = Ticket(
        id="T10",
        title="A Markdown ticket",
        status="todo",
        files=["src/legacy.py"],
        filePath=project.ticketsDir / "T10.md",
        bodyMarkdown="",
        bodyHtml="",
        raw={"id": "T10", "path": "docs/planning/tickets/T10.md"},
    )

    enriched = enrich_ticket(project, stub)

    assert enriched.content is None
    assert enriched.files == ["src/legacy.py"]


def test_two_fixture_tickets_share_a_critical_file() -> None:
    # Not decoration: the overlap is what the factory's filter reads to keep two lanes
    # off one path. A fixture without one would not exercise the field's purpose.
    project = _fixture_project()
    files = {
        stub.id: set(enrich_ticket(project, stub).files) for stub in iter_ticket_stubs(project)
    }

    assert files["T01"] & files["T02"] == {"src/foundation/entry.py"}


# --------------------------------------------------------------------------- #
# The Pydantic model really is the schema
# --------------------------------------------------------------------------- #


def test_render_is_a_pure_function_of_content_and_entry() -> None:
    # Rendering must not touch the filesystem or the clock: the cross-repo contract test
    # compares this output against `factory-ticket render`, and a renderer with a hidden
    # input cannot be compared to anything.
    content = parse_ticket_content("T09", json.dumps(VALID))
    entry = {"title": "Auth endpoint", "milestone": "v1.0"}

    assert render_ticket_markdown(content, entry) == render_ticket_markdown(content, entry)
