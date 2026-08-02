"""Unit tests for the read-only lane-artifact reader (results, receipts, last-stop).

The successful-parse cases read ``tests/fixtures/runs/{result,receipt}.json`` from
disk rather than an inline string, so the happy path is exercised against real
bytes. Those fixtures are ILLUSTRATIVE shapes, not captures of a real factory run
(see that directory's README) — which is exactly why nothing here asserts a field
NAME as a contract: T88 reads these artifacts as untyped objects and models no
schema, so the tests assert the parse, not the vocabulary.

The load-bearing assertions are the ones that keep the four skip reasons apart:
an artifact that is missing, one that is there and could not be read, and one
that was read and made no sense must never share a value, or a UI above can
render "the factory has not run here" for a lane whose result exists.
"""

import json
from pathlib import Path

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

from factory_console.domain.runs import ArtifactRead
from factory_console.file_adapter import runs as runs_module
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.runs import (
    LAST_STOP_RELATIVE_PATH,
    MAX_ARTIFACT_BYTES,
    RECEIPTS_RELATIVE_DIR,
    RESULTS_RELATIVE_DIR,
    read_last_stop,
    read_receipt,
    read_result,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "runs"

TICKET_ID = "T88"


def _write_artifact(project_root: Path, relative: Path, text: str) -> Path:
    """Write ``text`` at ``project_root / relative`` and return the created path."""
    path = project_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _result_relative(ticket_id: str = TICKET_ID) -> Path:
    return RESULTS_RELATIVE_DIR / f"{ticket_id}.json"


def _receipt_relative(ticket_id: str = TICKET_ID) -> Path:
    return RECEIPTS_RELATIVE_DIR / f"{ticket_id}.json"


# --------------------------------------------------------------------------- #
# A valid artifact parses into an untyped object
# --------------------------------------------------------------------------- #


def test_a_valid_result_parses_its_fields(tmp_path: Path) -> None:
    fixture = (FIXTURES / "result.json").read_text(encoding="utf-8")
    path = _write_artifact(tmp_path, _result_relative(), fixture)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason is None, "a readable JSON object is not a skip"
    assert result.data == json.loads(fixture), "every field is carried through verbatim"
    assert result.path == path.resolve()


def test_a_valid_receipt_parses_its_fields(tmp_path: Path) -> None:
    fixture = (FIXTURES / "receipt.json").read_text(encoding="utf-8")
    _write_artifact(tmp_path, _receipt_relative(), fixture)

    receipt = read_receipt(tmp_path, TICKET_ID)

    assert receipt.reason is None
    assert receipt.data == json.loads(fixture)


def test_a_valid_last_stop_parses_and_needs_no_ticket_id(tmp_path: Path) -> None:
    # last-stop.json names no ticket, so the reader takes none.
    _write_artifact(tmp_path, LAST_STOP_RELATIVE_PATH, '{"reason":"budget","ticket":"T88"}')

    last_stop = read_last_stop(tmp_path)

    assert last_stop.reason is None
    assert last_stop.data == {"reason": "budget", "ticket": "T88"}


def test_an_empty_json_object_is_data_not_a_skip(tmp_path: Path) -> None:
    # An artifact the factory wrote with nothing in it yet is EMPTY-BUT-VALID, and
    # must not be reported as a failure to read: ``{}`` is an answer.
    _write_artifact(tmp_path, _result_relative(), "{}")

    result = read_result(tmp_path, TICKET_ID)

    assert result.data == {}
    assert result.reason is None


def test_the_reader_does_not_model_the_artifacts_fields(tmp_path: Path) -> None:
    # T88 is the reading layer only: an artifact carrying fields no console model
    # names still parses whole. Modelling a schema (T89) must not be smuggled in
    # here, where there is no real captured artifact to verify field names against.
    _write_artifact(tmp_path, _result_relative(), '{"a_field_invented_tomorrow":{"nested":[1]}}')

    result = read_result(tmp_path, TICKET_ID)

    assert result.data == {"a_field_invented_tomorrow": {"nested": [1]}}


# --------------------------------------------------------------------------- #
# Absent: the ordinary state of a fresh clone (.factory/ is gitignored)
# --------------------------------------------------------------------------- #


def test_result_is_absent_when_the_project_has_no_results_dir(tmp_path: Path) -> None:
    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "absent"
    assert result.data is None
    assert isinstance(result, ArtifactRead), "absence is a RESULT, never a bare None"


def test_receipt_is_absent_when_the_project_has_no_receipts_dir(tmp_path: Path) -> None:
    receipt = read_receipt(tmp_path, TICKET_ID)

    assert receipt.reason == "absent"
    assert receipt.data is None


def test_last_stop_is_absent_when_no_file_exists(tmp_path: Path) -> None:
    last_stop = read_last_stop(tmp_path)

    assert last_stop.reason == "absent"
    assert last_stop.data is None


def test_result_is_absent_when_the_dir_exists_but_this_ticket_has_no_file(tmp_path: Path) -> None:
    # A project the factory HAS run on, for other tickets. Still absent for this id.
    _write_artifact(tmp_path, _result_relative("T99"), "{}")

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "absent"


def test_an_absent_artifact_still_names_the_path_it_was_about(tmp_path: Path) -> None:
    # The reason is only actionable together with WHICH file it is about.
    result = read_result(tmp_path, TICKET_ID)

    assert result.path == (tmp_path / _result_relative()).resolve()


# --------------------------------------------------------------------------- #
# Unparseable: read, and made no sense — a DIFFERENT reason from absent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("this is not json at all", id="not-json"),
        pytest.param('{"status":"ready"', id="truncated-mid-write"),
        pytest.param("", id="empty-file"),
    ],
)
def test_malformed_json_is_unparseable(tmp_path: Path, text: str) -> None:
    _write_artifact(tmp_path, _result_relative(), text)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "unparseable"
    assert result.data is None


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("[1, 2, 3]", id="bare-list"),
        pytest.param('"ready"', id="bare-string"),
        pytest.param("null", id="bare-null"),
        pytest.param("42", id="bare-number"),
    ],
)
def test_valid_json_that_is_not_an_object_is_unparseable(tmp_path: Path, text: str) -> None:
    # The artifact contract is one JSON OBJECT. Valid JSON of the wrong shape is a
    # failure to read the artifact, not an artifact with no fields.
    _write_artifact(tmp_path, _receipt_relative(), text)

    receipt = read_receipt(tmp_path, TICKET_ID)

    assert receipt.reason == "unparseable"
    assert receipt.data is None


def test_malformed_last_stop_is_unparseable(tmp_path: Path) -> None:
    _write_artifact(tmp_path, LAST_STOP_RELATIVE_PATH, "{oops")

    assert read_last_stop(tmp_path).reason == "unparseable"


def test_absent_and_malformed_are_distinct_reasons(tmp_path: Path) -> None:
    # THE distinction this result type exists for. Asserted as inequality of the
    # REASONS, not as "both are empty": a caller that can only see ``data is None``
    # would render "the factory never ran here" for a lane whose result is sitting
    # on disk, corrupt.
    absent_project = tmp_path / "fresh_clone"
    absent_project.mkdir()
    malformed_project = tmp_path / "corrupt"
    malformed_project.mkdir()
    _write_artifact(malformed_project, _result_relative(), "not json")

    absent = read_result(absent_project, TICKET_ID)
    malformed = read_result(malformed_project, TICKET_ID)

    assert absent.data is None and malformed.data is None, "both are empty..."
    assert absent.reason != malformed.reason, "...and must still be tellable apart"
    assert absent.reason == "absent"
    assert malformed.reason == "unparseable"


# --------------------------------------------------------------------------- #
# Unreadable: it is there and could not be read at all
# --------------------------------------------------------------------------- #


def test_a_directory_at_the_artifact_path_is_unreadable_not_unparseable(tmp_path: Path) -> None:
    # A directory stats fine and then fails to read — the shape of any I/O failure
    # (permission denied, an EIO). The reason must say the file could not be read:
    # nothing ever examined its contents, and ``unparseable`` would send a human
    # hunting a syntax error in a file nothing could open.
    (tmp_path / _result_relative()).mkdir(parents=True)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "unreadable"
    assert result.data is None


def test_a_directory_at_the_receipt_path_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / _receipt_relative()).mkdir(parents=True)

    assert read_receipt(tmp_path, TICKET_ID).reason == "unreadable"


def test_a_directory_at_the_last_stop_path_is_unreadable(tmp_path: Path) -> None:
    (tmp_path / LAST_STOP_RELATIVE_PATH).mkdir(parents=True)

    assert read_last_stop(tmp_path).reason == "unreadable"


def test_unreadable_is_distinct_from_absent_and_unparseable(tmp_path: Path) -> None:
    absent_project = tmp_path / "absent"
    absent_project.mkdir()
    unreadable_project = tmp_path / "unreadable"
    (unreadable_project / _result_relative()).mkdir(parents=True)
    unparseable_project = tmp_path / "unparseable"
    unparseable_project.mkdir()
    _write_artifact(unparseable_project, _result_relative(), "[]")

    reasons = {
        read_result(absent_project, TICKET_ID).reason,
        read_result(unreadable_project, TICKET_ID).reason,
        read_result(unparseable_project, TICKET_ID).reason,
    }

    assert reasons == {"absent", "unreadable", "unparseable"}, "three failures, three reasons"


# --------------------------------------------------------------------------- #
# The read is bounded
# --------------------------------------------------------------------------- #


def test_over_cap_artifact_is_reported_not_silently_short_read(tmp_path: Path) -> None:
    path = tmp_path / _result_relative()
    path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.write(b'{"status":"ready"}')
        # Sparse-extend past the cap rather than materialising a MiB of bytes.
        handle.truncate(MAX_ARTIFACT_BYTES + 1)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "too_large"
    assert result.data is None, "over the cap NOTHING is parsed — not the readable prefix"


def test_an_artifact_exactly_at_the_cap_is_still_read(tmp_path: Path) -> None:
    body = b'{"status":"ready"}'
    path = tmp_path / _receipt_relative()
    path.parent.mkdir(parents=True)
    # Pad inside the JSON with whitespace so the file is exactly the cap and still
    # parses: exactly at the cap is UNDER the cap.
    path.write_bytes(body + b" " * (MAX_ARTIFACT_BYTES - len(body)))

    receipt = read_receipt(tmp_path, TICKET_ID)

    assert receipt.reason is None
    assert receipt.data == {"status": "ready"}


def test_the_cap_reason_is_distinct_from_unparseable(tmp_path: Path) -> None:
    # An oversized file is not a corrupt one: the bytes were never looked at.
    path = tmp_path / LAST_STOP_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.truncate(MAX_ARTIFACT_BYTES + 1)

    assert read_last_stop(tmp_path).reason == "too_large"


# --------------------------------------------------------------------------- #
# Path safety — an unsafe id is refused BEFORE any filesystem access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_id",
    [
        pytest.param("..", id="dotdot"),
        pytest.param(".", id="dot"),
        pytest.param("../../etc/passwd", id="traversal"),
        pytest.param("foo/bar", id="slash-segment"),
        pytest.param("", id="empty"),
        pytest.param("T88\n", id="trailing-newline"),
    ],
)
@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_an_unsafe_ticket_id_is_refused(tmp_path: Path, reader, bad_id: str) -> None:
    # The artifact directories EXIST, so a raised PathTraversal proves it is the id
    # validation firing and not a missing directory.
    (tmp_path / RESULTS_RELATIVE_DIR).mkdir(parents=True)
    (tmp_path / RECEIPTS_RELATIVE_DIR).mkdir(parents=True)

    with pytest.raises(PathTraversal):
        reader(tmp_path, bad_id)


@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_an_unsafe_id_is_refused_before_the_filesystem_is_touched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reader
) -> None:
    # Not merely "it raises": it must raise without ever stat'ing or reading, so an
    # unsafe id cannot probe the filesystem for existence on its way to the refusal.
    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an unsafe ticket id must not reach the filesystem")

    monkeypatch.setattr(Path, "stat", _forbidden)
    monkeypatch.setattr(Path, "read_bytes", _forbidden)

    with pytest.raises(PathTraversal):
        reader(tmp_path, "../../etc/passwd")


@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_an_id_whose_resolved_path_escapes_the_project_root_is_refused(
    tmp_path: Path, reader
) -> None:
    # The second gate, which no amount of id validation can cover: the id is a
    # perfectly ordinary segment and the artifact DIRECTORY is a symlink out of the
    # project, so the resolved file sits outside the root.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "T88.json").write_text('{"leaked":true}', encoding="utf-8")
    project_root = tmp_path / "project"
    (project_root / ".factory").mkdir(parents=True)
    (project_root / RESULTS_RELATIVE_DIR).symlink_to(outside, target_is_directory=True)
    (project_root / RECEIPTS_RELATIVE_DIR).symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathTraversal):
        reader(project_root, TICKET_ID)


def test_the_refusal_uses_the_uniform_invalid_ticket_id_contract() -> None:
    # This module must reuse the SHARED PathTraversal, not define a second class:
    # one ``except PathTraversal`` at the edge layer has to catch every unsafe-id
    # path, and the REST error code has to be identical whichever module refused.
    from factory_console.file_adapter.run_state import PathTraversal as RunStatePathTraversal

    exc = PathTraversal("../etc/passwd")
    assert exc.code == "invalid_ticket_id"
    assert exc.status == 400
    assert runs_module.PathTraversal is RunStatePathTraversal


def test_read_last_stop_takes_no_ticket_id_so_it_cannot_traverse(tmp_path: Path) -> None:
    # No id, no traversal surface, and therefore no PathTraversal path at all —
    # the call is total for every project root.
    assert read_last_stop(tmp_path).reason == "absent"


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# --------------------------------------------------------------------------- #


def test_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(runs_module)


def test_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(runs_module)
