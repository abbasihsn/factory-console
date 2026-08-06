"""Unit tests for the shared bounded-read sequence.

:func:`~factory_console.file_adapter.bounded_read.read_bounded` is the ONE
open/fstat/``S_ISREG``/cap/bounded-read sequence consumed by both
:func:`~factory_console.file_adapter.ledger.read_ledger` and
:func:`~factory_console.file_adapter.runs._read_json_artifact` (see T97). These
tests assert the THREAT MODEL the sequence exists to close — a FIFO and a
directory refused rather than blocked or short-read, and an over-cap file
reported as a named skip rather than a silent truncation — not merely that the
refactor preserved today's callers' behaviour.
"""

import os
import stat as stat_module
from pathlib import Path

import pytest
from _read_only_guard import assert_module_carries_read_only_header, assert_module_is_read_only

from factory_console.file_adapter import bounded_read as bounded_read_module
from factory_console.file_adapter.bounded_read import read_bounded


def test_a_regular_file_under_the_cap_reads_whole(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_bytes(b'{"a":1}')

    result = read_bounded(path, max_bytes=1024, label="test")

    assert result.outcome == "ok"
    assert result.data == b'{"a":1}'


def test_a_missing_file_is_not_found_and_not_logged_by_this_function(tmp_path: Path) -> None:
    result = read_bounded(tmp_path / "missing.json", max_bytes=1024, label="test")

    assert result.outcome == "not_found"
    assert result.data == b""


def test_a_fifo_is_refused_rather_than_blocking_forever(tmp_path: Path) -> None:
    # THE reason this function opens once and interrogates the DESCRIPTOR. A FIFO
    # stat's as ``st_size == 0``, so a name-based size check waves it past the cap,
    # and the read that follows blocks FOREVER waiting for a writer that never
    # comes. O_NONBLOCK makes the open total and S_ISREG refuses the node.
    #
    # If this test ever HANGS instead of failing, that is the regression.
    if not hasattr(os, "mkfifo"):
        pytest.skip("platform has no FIFOs")
    path = tmp_path / "fifo"
    os.mkfifo(path)

    result = read_bounded(path, max_bytes=1024, label="test")

    assert result.outcome == "unreadable"
    assert result.data == b""


def test_a_directory_is_refused_not_read(tmp_path: Path) -> None:
    # O_RDONLY does not fail on a directory (EISDIR is for O_WRONLY/O_RDWR), so the
    # open succeeds and the S_ISREG check on the opened descriptor is the one thing
    # that refuses it.
    path = tmp_path / "adir"
    path.mkdir()

    result = read_bounded(path, max_bytes=1024, label="test")

    assert result.outcome == "unreadable"
    assert result.data == b""


def test_an_over_cap_file_is_reported_not_silently_short_read(tmp_path: Path) -> None:
    path = tmp_path / "big.json"
    with path.open("wb") as handle:
        handle.write(b"x")
        handle.truncate(2048 + 1)

    result = read_bounded(path, max_bytes=2048, label="test")

    assert result.outcome == "too_large"
    assert result.data == b"", "over the cap NOTHING is returned — not a truncated prefix"


def test_a_file_exactly_at_the_cap_is_still_read(tmp_path: Path) -> None:
    path = tmp_path / "exact.json"
    path.write_bytes(b"x" * 2048)

    result = read_bounded(path, max_bytes=2048, label="test")

    assert result.outcome == "ok"
    assert len(result.data) == 2048


def test_a_file_that_grows_past_the_cap_after_the_stat_is_still_capped(
    monkeypatch, tmp_path: Path
) -> None:
    # The cap must bound the READ, not an fstat that is already stale when it is
    # acted on. Pinned by DESCRIPTOR, not by path, so a symlinked tmp dir cannot
    # leave this fake unfired.
    path = tmp_path / "grows.json"
    with path.open("wb") as handle:
        handle.write(b"x")
        handle.truncate(2048 + 1)

    real_fstat = os.fstat
    pinned: list[int] = []

    class _SmallStat:
        st_mode = stat_module.S_IFREG | 0o644
        st_size = 18

    def _stale_fstat(descriptor: int) -> object:
        info = real_fstat(descriptor)
        if stat_module.S_ISREG(info.st_mode) and info.st_size == 2048 + 1:
            pinned.append(descriptor)
            return _SmallStat()
        return info

    monkeypatch.setattr(os, "fstat", _stale_fstat)

    result = read_bounded(path, max_bytes=2048, label="test")

    assert pinned, "the stale-size fake must actually have fired, or this proves nothing"
    assert result.outcome == "too_large"
    assert result.data == b""


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# --------------------------------------------------------------------------- #


def test_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(bounded_read_module)


def test_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(bounded_read_module)
