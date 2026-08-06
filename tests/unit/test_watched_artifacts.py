"""Unit tests for the ONE shared watched-artefact list.

:data:`~factory_console.domain.watched_artifacts.WATCHED_JSON_ARTIFACTS` is the
single place a reader's path constant and the watcher's schedule meet (T95), so what
it CONTAINS is a contract rather than an implementation detail. The watcher's own
behaviour is pinned end-to-end in ``tests/integration/test_real_file_watcher.py``;
these tests pin the list's shape and membership, which is what "read but never
watched" was an absence FROM.
"""

from pathlib import Path

from factory_console.domain.watched_artifacts import (
    LAST_STOP_RELATIVE_PATH,
    LEDGER_RELATIVE_PATH,
    RECEIPTS_RELATIVE_DIR,
    RESULTS_RELATIVE_DIR,
    WATCHED_JSON_ARTIFACTS,
)
from factory_console.file_adapter import runs


def test_every_entry_carries_a_scope_a_relative_path_and_a_kind() -> None:
    for scope, relative, kind in WATCHED_JSON_ARTIFACTS:
        assert isinstance(scope, str) and scope
        assert isinstance(relative, Path)
        assert not relative.is_absolute(), "a watched path is project-relative, never absolute"
        assert kind in {"file", "dir"}


def test_the_runs_artifacts_are_watched_with_the_kind_that_matches_them() -> None:
    # T99: results and receipts are DIRECTORIES of ``<ticket_id>.json`` files whose
    # names no constant can spell, so they are matched by directory identity; last
    # stop is one file at a fixed name, like run-state.json and the ledger.
    entries = {relative: (scope, kind) for scope, relative, kind in WATCHED_JSON_ARTIFACTS}
    assert entries[RESULTS_RELATIVE_DIR] == ("runs", "dir")
    assert entries[RECEIPTS_RELATIVE_DIR] == ("runs", "dir")
    assert entries[LAST_STOP_RELATIVE_PATH] == ("runs", "file")


def test_the_earlier_artifacts_keep_their_scope_and_kind() -> None:
    # T95's criterion 4 is not narrowed by T99: the ledger and the JSON run-state
    # source stay in the same list, under the same scopes, matched the same way.
    entries = {relative: (scope, kind) for scope, relative, kind in WATCHED_JSON_ARTIFACTS}
    assert entries[LEDGER_RELATIVE_PATH] == ("ledger", "file")
    assert entries[Path(".factory") / "run-state.json"] == ("run-state", "file")


def test_the_runs_reader_takes_its_paths_from_this_list() -> None:
    # The whole mechanism: a reader that declared its own literal is a reader the
    # watcher can be ignorant of. These must be the SAME objects, not equal copies.
    assert runs.RESULTS_RELATIVE_DIR is RESULTS_RELATIVE_DIR
    assert runs.RECEIPTS_RELATIVE_DIR is RECEIPTS_RELATIVE_DIR
    assert runs.LAST_STOP_RELATIVE_PATH is LAST_STOP_RELATIVE_PATH
