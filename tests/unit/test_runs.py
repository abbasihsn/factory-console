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
import os
import stat as stat_module
from pathlib import Path

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)
from pydantic import ValidationError

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
# The result type's own invariant: data XOR reason, enforced not documented
# --------------------------------------------------------------------------- #


def test_an_artifact_read_cannot_be_constructed_with_neither_outcome() -> None:
    # Both fields default to None, so this is the shape a consumer or a test double
    # writes for a case it forgot to name — and it is exactly the unnamed empty this
    # module exists to abolish. A caller branching on ``reason is None`` would read it
    # as a clean read and subscript ``data`` into a TypeError.
    with pytest.raises(ValidationError, match="exactly one of data or reason"):
        ArtifactRead(path=Path("/tmp/x.json"))


def test_an_artifact_read_cannot_be_constructed_with_both_outcomes() -> None:
    # The other impossible combination: read successfully AND skipped.
    with pytest.raises(ValidationError, match="exactly one of data or reason"):
        ArtifactRead(path=Path("/tmp/x.json"), data={"a": 1}, reason="absent")


def test_an_empty_json_object_is_a_successful_read_not_an_unnamed_empty() -> None:
    # ``data={}`` is falsy but NOT None, so the invariant must be tested with ``is
    # None`` and never for truthiness — an artifact that is legitimately ``{}`` is a
    # successful read, and rejecting it here would make a valid file unrepresentable.
    read = ArtifactRead(path=Path("/tmp/x.json"), data={})

    assert read.reason is None
    assert read.data == {}


@pytest.mark.parametrize("reason", ["absent", "unreadable", "unparseable", "too_large"])
def test_each_reason_constructs_without_data(reason: str) -> None:
    read = ArtifactRead(path=Path("/tmp/x.json"), reason=reason)

    assert read.data is None
    assert read.reason == reason


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
    # A directory stats fine and is not a regular file. The reason must say the file
    # could not be read: nothing ever examined its contents, and ``unparseable``
    # would send a human hunting a syntax error in a file nothing could open.
    # Permission-denied and EIO are the same reason by a different route; they are
    # covered separately below, since this case never raises from the read at all.
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


def test_a_permission_denied_open_is_unreadable_never_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # THE fail-quiet this result type exists to prevent, and the one an easy refactor
    # reintroduces: ``FileNotFoundError``/``NotADirectoryError`` sit in the sibling
    # except clause, so widening that clause to swallow ``PermissionError`` would make
    # a result the console cannot read report as "the factory never ran here".
    # Monkeypatched rather than chmod'd so the assertion is about THIS module's errno
    # split and not about the platform's, which is not uniform.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')

    def _denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "open", _denied)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "unreadable", "'I could not look' is never 'nothing is there'"
    assert result.data is None


def test_a_permission_denied_fstat_is_unreadable_never_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The same split one step later: the open succeeds and interrogating the DESCRIPTOR
    # is refused. Split from the open case because the two sit in different try blocks
    # and only one of them has an ``absent`` clause to be wrongly widened into.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')

    def _denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "fstat", _denied)

    assert read_result(tmp_path, TICKET_ID).reason == "unreadable"


def test_an_io_error_at_the_read_itself_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The split one step later again: the open and the fstat both succeeded and the
    # failure is on the held DESCRIPTOR. It has its own try block, and the module's
    # comment there records that ``absent`` was deliberately DELETED from it — an
    # unlinked file keeps reading through the descriptor — so a refactor that restored
    # an ``absent`` clause here would report "the factory never ran" for a failing disk.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')

    class _FailingRead:
        """Stands in for the wrapper without opening one — the reader owns the fd."""

        def __enter__(self) -> "_FailingRead":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self, *_args: object) -> bytes:
            raise OSError(5, "Input/output error")

    def _failing_fdopen(*_args: object, **_kwargs: object) -> "_FailingRead":
        return _FailingRead()

    monkeypatch.setattr(os, "fdopen", _failing_fdopen)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "unreadable"
    assert result.data is None


def test_a_failing_close_does_not_replace_the_answer_already_computed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The one hole the module's own docstring names in its NEVER-raises contract:
    # ``close(2)`` reports deferred errors (EIO on NFS and some FUSE mounts), and a
    # raise from the ``finally`` REPLACES the ArtifactRead the branches above already
    # computed. The escaping OSError is not a FactoryConsoleError, so the edge layer has
    # no handler and it surfaces as an unmapped 500 — on a read that SUCCEEDED.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')
    real_close = os.close

    def _failing_close(descriptor: int) -> None:
        real_close(descriptor)
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "close", _failing_close)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason is None, "a failed close cannot change an answer already decided"
    assert result.data == {"status": "ready"}


def test_an_artifact_that_vanishes_before_the_open_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The race the module names: the factory rewrites these files while the console is
    # running, so a file can be enumerated and then be gone by the time it is opened. It
    # is still "there is nothing to find", NOT "I could not look" — routing it to
    # ``unreadable`` would report a degradation for an ordinary rewrite. Mocked because
    # a real race is not reproducible deterministically.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')

    def _vanished(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(os, "open", _vanished)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "absent"
    assert result.data is None


def test_unlinking_an_open_artifact_does_not_make_it_absent(tmp_path: Path) -> None:
    # The counterpart to the test above, and the reason the mid-read ``absent`` clause
    # was DELETED rather than moved: once the descriptor is held, the file cannot vanish
    # out from under the read. Unlinking drops the name only; the inode is still there
    # and still readable. A future refactor back to reading by name would reintroduce
    # the window this asserts is closed, and would fail here.
    path = _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')

    real_fstat = os.fstat
    unlinked: list[Path] = []

    def _unlink_then_fstat(descriptor: int) -> object:
        # Fires between the open and the read, which is exactly the window that used to
        # be a name lookup.
        if not unlinked:
            unlinked.append(path)
            path.unlink()
        return real_fstat(descriptor)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "fstat", _unlink_then_fstat)
        result = read_result(tmp_path, TICKET_ID)

    assert unlinked, "the artifact must actually have been unlinked mid-read"
    assert result.reason is None, "an open descriptor outlives the name it was opened by"
    assert result.data == {"status": "ready"}


def test_a_path_that_cannot_be_encoded_is_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # ``os.open`` answers a path it cannot encode (an embedded NUL) with ``ValueError``,
    # NOT an ``OSError``, so the errno clauses do not cover it. Without its own clause
    # the NEVER-raises contract breaks and the reader 500s. Monkeypatched because a
    # ``Path`` carrying a real NUL cannot be constructed and written through pathlib.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')

    def _unencodable(*_args: object, **_kwargs: object) -> None:
        raise ValueError("embedded null byte")

    monkeypatch.setattr(os, "open", _unencodable)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "unreadable", "'I could not look' — nothing was ever examined"
    assert result.data is None


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(RecursionError("maximum recursion depth exceeded"), id="recursion"),
        pytest.param(MemoryError(), id="memory"),
    ],
)
def test_a_pathological_document_is_unparseable_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: BaseException
) -> None:
    # ``json.loads`` answers pathological input with exceptions OUTSIDE ``ValueError``:
    # deeply nested arrays raise ``RecursionError``, a huge document ``MemoryError``.
    # Narrowing the except clause back to ``ValueError`` alone is an easy mistake to
    # make (``JSONDecodeError`` IS a ``ValueError``, so the obvious tests still pass)
    # and would break the NEVER-raises contract, 500ing every request naming this
    # ticket until the file changed. Raised directly rather than by building a genuinely
    # pathological payload, so the test is fast and deterministic.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')

    def _pathological(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(runs_module.json, "loads", _pathological)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "unparseable"
    assert result.data is None


@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_a_project_root_that_will_not_resolve_is_refused_not_assumed_contained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reader
) -> None:
    # ``_within_root`` returns None when the ROOT itself cannot be resolved — the
    # containment question could not be put. That must be a refusal: treating an
    # unanswered question as "contained" is the fail-open this gate exists to prevent.
    # Distinct from the escape case, which is PROVEN and raises.
    _write_artifact(tmp_path, _result_relative(), '{"status":"ready"}')
    _write_artifact(tmp_path, _receipt_relative(), '{"verdict":"pass"}')

    real_resolve = Path.resolve

    def _root_will_not_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self == tmp_path:
            raise RuntimeError(f"Symlink loop from {self}")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _root_will_not_resolve)

    result = reader(tmp_path, TICKET_ID)

    assert result.reason == "unreadable", "an unanswerable containment question is a refusal"
    assert result.data is None


def test_a_project_root_that_will_not_resolve_refuses_last_stop_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The same gate on the reader that has no id to validate, so the two cannot drift
    # in what counts as an unanswerable containment question.
    _write_artifact(tmp_path, LAST_STOP_RELATIVE_PATH, '{"reason":"merged"}')

    real_resolve = Path.resolve

    def _root_will_not_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if self == tmp_path:
            raise RuntimeError(f"Symlink loop from {self}")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", _root_will_not_resolve)

    result = read_last_stop(tmp_path)

    assert result.reason == "unreadable"
    assert result.data is None


def test_a_file_where_a_parent_directory_belongs_is_absent(tmp_path: Path) -> None:
    # ``.factory/results`` is a FILE, so stat'ing ``.factory/results/T88.json`` raises
    # NotADirectoryError. Nothing is there to be hiding anything, so this is absent —
    # routing it to ``unreadable`` would sound like a permissions problem and send an
    # operator chasing one that does not exist.
    (tmp_path / ".factory").mkdir()
    (tmp_path / RESULTS_RELATIVE_DIR).write_text("not a directory", encoding="utf-8")

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "absent"
    assert result.data is None


def test_a_non_regular_file_is_refused_without_blocking(tmp_path: Path) -> None:
    # A FIFO stats as size 0, so it passes the size cap — and opening it for reading
    # BLOCKS until a writer appears, hanging the request with no timeout. Two things
    # keep that from happening: the open carries ``O_NONBLOCK`` so it returns
    # immediately, and the node type is then read off the DESCRIPTOR and refused. If
    # this test ever hangs instead of failing, that is the regression.
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform has no FIFOs")
    path = tmp_path / _result_relative()
    path.parent.mkdir(parents=True)
    os.mkfifo(path)

    result = read_result(tmp_path, TICKET_ID)

    assert result.reason == "unreadable", "a size bounds a regular file and nothing else"
    assert result.data is None


def test_a_non_regular_last_stop_is_refused_too(tmp_path: Path) -> None:
    # The same node-type gate on the reader that has no id to validate. A device node
    # would be the other shape of this (``/dev/zero`` stats as size 0 and never reaches
    # EOF, so an unbounded read exhausts memory); one reached through a SYMLINK is
    # already refused a step earlier by the containment gate, and creating one inside
    # the root needs privileges a test must not assume — so the FIFO is what exercises
    # this branch for a CONTAINED path.
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform has no FIFOs")
    (tmp_path / ".factory").mkdir()
    os.mkfifo(tmp_path / LAST_STOP_RELATIVE_PATH)

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


def test_a_file_that_grows_past_the_cap_after_the_stat_is_still_capped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The cap must bound the READ, not an fstat that is already stale when it is acted
    # on: the factory rewrites these files while the console is running, so a file
    # that stats small and is extended before the read would otherwise be pulled into
    # memory whole — the unbounded read on a request path the cap exists to prevent.
    # The fstat is pinned to a small size while the file on disk is over the cap.
    #
    # Pinned by DESCRIPTOR, not by path. An earlier revision keyed the fake on
    # ``self == path`` while the module stat'd the RESOLVED path, so on any platform
    # whose tmp dir contains a symlink component (macOS: /var -> /private/var) the fake
    # never fired, the real size was already over the cap, and the assertion below was
    # satisfied by the pre-read check — leaving the read-time cap, the only thing this
    # test names, unexercised while still green.
    path = tmp_path / _result_relative()
    path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.write(b'{"status":"ready"}')
        handle.truncate(MAX_ARTIFACT_BYTES + 1)

    real_fstat = os.fstat
    pinned: list[int] = []

    class _SmallStat:
        st_mode = stat_module.S_IFREG | 0o644
        st_size = 18

    def _stale_fstat(descriptor: int) -> object:
        info = real_fstat(descriptor)
        if stat_module.S_ISREG(info.st_mode) and info.st_size == MAX_ARTIFACT_BYTES + 1:
            pinned.append(descriptor)
            return _SmallStat()
        return info

    monkeypatch.setattr(os, "fstat", _stale_fstat)

    result = read_result(tmp_path, TICKET_ID)

    assert pinned, "the stale-size fake must actually have fired, or this proves nothing"
    assert result.reason == "too_large", "the cap is a property of the read, not of the stat"
    assert result.data is None, "never parsed from the truncated prefix"


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

    # Patch what the module ACTUALLY calls. An earlier revision guarded
    # ``Path.read_bytes``, which this module has never called — an inert guard that
    # made the test look stricter than it was, leaving the real read entry point
    # unwatched.
    monkeypatch.setattr(os, "open", _forbidden)
    monkeypatch.setattr(os, "fstat", _forbidden)
    monkeypatch.setattr(Path, "stat", _forbidden)

    with pytest.raises(PathTraversal):
        reader(tmp_path, "../../etc/passwd")


@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_an_id_whose_resolved_path_escapes_the_project_root_is_unreadable(
    tmp_path: Path, reader
) -> None:
    # The second gate, which no amount of id validation can cover: the id is a
    # perfectly ordinary segment and the artifact DIRECTORY is a symlink out of the
    # project, so the resolved file sits outside the root. The escape is refused —
    # out-of-project content is never returned — but it is refused as ``unreadable``
    # and NOT as ``invalid_ticket_id``: the id was proven well-formed one line
    # earlier, so blaming it would send an operator to check a value they will find
    # correct while the symlinked ``.factory`` tree, which is the actual cause, goes
    # uninspected. It is the same condition ``read_last_stop`` meets with no id at
    # all, and it gets the same answer here.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "T88.json").write_text('{"leaked":true}', encoding="utf-8")
    project_root = tmp_path / "project"
    (project_root / ".factory").mkdir(parents=True)
    try:
        (project_root / RESULTS_RELATIVE_DIR).symlink_to(outside, target_is_directory=True)
        (project_root / RECEIPTS_RELATIVE_DIR).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not support symlinks")

    escaped = reader(project_root, TICKET_ID)

    assert escaped.data is None, "out-of-project content is never returned"
    assert escaped.reason == "unreadable", "it is there and this console will not look"


@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_a_symlink_loop_is_unreadable_and_does_not_escape_as_a_crash(
    tmp_path: Path, reader
) -> None:
    # ``Path.resolve(strict=False)`` is not total: through CPython 3.12 it re-stats the
    # resolved path and turns ELOOP into a RuntimeError, which 3.13 dropped. Both are
    # inside this project's supported range, so an unhandled loop is an unmapped 500
    # on one interpreter and a clean skip on the other, for identical bytes on disk.
    # It is NOT a PathTraversal: the id is well-formed and nothing was proven unsafe.
    for relative in (RESULTS_RELATIVE_DIR, RECEIPTS_RELATIVE_DIR):
        (tmp_path / relative).mkdir(parents=True)
        try:
            (tmp_path / relative / f"{TICKET_ID}.json").symlink_to(f"{TICKET_ID}.json")
        except (OSError, NotImplementedError):
            pytest.skip("platform does not support symlinks")

    result = reader(tmp_path, TICKET_ID)

    assert result.reason == "unreadable"
    assert result.data is None


def test_a_symlinked_last_stop_is_not_read_through_out_of_the_project(tmp_path: Path) -> None:
    # read_last_stop takes no ticket id, which removes the id-validation surface and
    # NOT the containment one: console-owned constants fix the path asked for, not the
    # file it lands on. A symlink here (a committed one in an untrusted checkout, or
    # anything the factory process writes) would otherwise return any JSON object the
    # server can open as this project's last-stop record — with ``reason is None``
    # marking it a clean read.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "creds.json").write_text('{"token":"SECRET"}', encoding="utf-8")
    project_root = tmp_path / "project"
    (project_root / ".factory").mkdir(parents=True)
    try:
        (project_root / LAST_STOP_RELATIVE_PATH).symlink_to(outside / "creds.json")
    except (OSError, NotImplementedError):
        pytest.skip("platform does not support symlinks")

    last_stop = read_last_stop(project_root)

    assert last_stop.data is None, "out-of-project content is never returned"
    assert last_stop.reason == "unreadable", "it is there and this console will not look"


def test_a_symlinked_factory_dir_does_not_leak_last_stop_either(tmp_path: Path) -> None:
    # The escape one level up: ``.factory`` itself is the symlink, so every constant
    # under it resolves outside the root.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "last-stop.json").write_text('{"reason":"leaked"}', encoding="utf-8")
    project_root = tmp_path / "project"
    project_root.mkdir()
    try:
        (project_root / ".factory").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not support symlinks")

    assert read_last_stop(project_root).reason == "unreadable"


def test_last_stop_reports_a_resolved_path_like_the_other_two_readers(tmp_path: Path) -> None:
    # ``path`` is what makes a reason actionable, so it must mean the same thing for
    # all three artifacts. discover_project can hand back an unresolved root, and the
    # ticket-id readers always resolve — last-stop must not be the odd one out.
    _write_artifact(tmp_path, LAST_STOP_RELATIVE_PATH, '{"reason":"budget"}')

    last_stop = read_last_stop(tmp_path)

    assert last_stop.reason is None
    assert last_stop.path == (tmp_path / LAST_STOP_RELATIVE_PATH).resolve()


def test_the_refusal_uses_the_uniform_invalid_ticket_id_contract(tmp_path: Path) -> None:
    # This module must reuse the SHARED PathTraversal, not define a second class:
    # one ``except PathTraversal`` at the edge layer has to catch every unsafe-id
    # path, and the REST error code has to be identical whichever module refused.
    # Asserted on what a reader actually RAISES rather than on a name imported into
    # runs.py — the refusal now comes from the shared validator, so the module no
    # longer names the class itself and an attribute check would pin an import
    # instead of the contract.
    from factory_console.file_adapter.run_state import PathTraversal as RunStatePathTraversal

    with pytest.raises(RunStatePathTraversal) as refused:
        read_result(tmp_path, "../../etc/passwd")

    assert refused.value.code == "invalid_ticket_id"
    assert refused.value.status == 400


@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_a_pattern_violating_id_is_refused_in_the_same_words_everywhere(tmp_path: Path, reader):
    # The point of routing a pattern violation through ``from_pattern_violation``: the
    # same id rejected by this reader, by the run-state probe and at the HTTP boundary
    # has to produce a word-identical envelope. Only the shared classmethod makes that
    # true, and nothing else asserted the MESSAGE — every other path-safety test here
    # stops at the exception type, which the generic reason would satisfy just as well.
    from factory_console.file_adapter.run_state import probe_ticket_state

    (tmp_path / RESULTS_RELATIVE_DIR).mkdir(parents=True)
    (tmp_path / RECEIPTS_RELATIVE_DIR).mkdir(parents=True)

    with pytest.raises(PathTraversal) as from_reader:
        reader(tmp_path, "foo/bar")
    with pytest.raises(PathTraversal) as from_probe:
        probe_ticket_state(tmp_path, "foo/bar")

    assert from_reader.value.message == PathTraversal.from_pattern_violation("foo/bar").message
    assert from_reader.value.message == from_probe.value.message
    assert from_reader.value.details == {"ticketId": "foo/bar"}


@pytest.mark.parametrize("bad_id", [".", ".."], ids=["dot", "dotdot"])
@pytest.mark.parametrize("reader", [read_result, read_receipt], ids=["result", "receipt"])
def test_a_bare_dot_id_keeps_the_generic_reason_because_it_matches_the_pattern(
    tmp_path: Path, reader, bad_id: str
) -> None:
    # The other half of the deliberate two-message split. ``.`` and ``..`` SATISFY
    # TICKET_ID_PATTERN, so telling an operator the id "must match ^[A-Za-z0-9_.-]+$"
    # would send them to fix an id that is already well-formed.
    (tmp_path / RESULTS_RELATIVE_DIR).mkdir(parents=True)
    (tmp_path / RECEIPTS_RELATIVE_DIR).mkdir(parents=True)

    with pytest.raises(PathTraversal) as refused:
        reader(tmp_path, bad_id)

    assert refused.value.message != PathTraversal.from_pattern_violation(bad_id).message


@pytest.mark.parametrize(
    ("reader", "relative_dir"),
    [(read_result, RESULTS_RELATIVE_DIR), (read_receipt, RECEIPTS_RELATIVE_DIR)],
    ids=["result", "receipt"],
)
def test_a_well_formed_id_against_a_symlinked_factory_is_never_an_invalid_ticket_id(
    tmp_path: Path, reader, relative_dir: Path
) -> None:
    # The converse of the containment refusal, stated explicitly because it is the
    # thing that was wrong: ``invalid_ticket_id`` is reserved for an id that is
    # actually invalid, and this id is not. An accusation about the wrong value is
    # worse than an unhelpful one — it sends the operator to fix the id and away from
    # the symlinked ``.factory`` that is the real cause. Also pins the ``path`` the
    # refusal reports: it is the RESOLVED path, as every other outcome's is, so a
    # caller keying artifacts by it gets one key per file.
    project_root = tmp_path / "project"
    (project_root / ".factory").mkdir(parents=True)
    outside = tmp_path / "outside" / relative_dir.name
    outside.mkdir(parents=True)
    try:
        (project_root / relative_dir).symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("platform does not support symlinks")
    (outside / f"{TICKET_ID}.json").write_text('{"status":"ready"}', encoding="utf-8")

    try:
        escaped = reader(project_root, TICKET_ID)
    except PathTraversal as refused:
        pytest.fail(f"a well-formed id must not be refused as {refused.code}: {refused.message}")

    assert escaped.reason == "unreadable"
    assert escaped.data is None
    assert escaped.path == (outside / f"{TICKET_ID}.json").resolve()


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
