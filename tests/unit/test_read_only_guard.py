"""Tests for the shared read-only AST guard itself.

The guard in ``tests/_read_only_guard.py`` is what pins the READ-ONLY contract on
``file_adapter.run_state``, ``ledger``, ``runs``, ``fake_writer`` and
``watcher_real``. It had no tests of its own, and its failure mode is SILENCE: a
mutation form it does not recognise makes every one of those modules' guard tests
pass green while the contract is broken. That is not hypothetical — the guard
recognised only the builtin ``open(file, mode)`` form, so ``path.open("w")`` in a
read-only module was invisible to it, and ``file_adapter.runs`` is the first
read-only module to open files by that route.

These tests assert the guard FIRES on each mutation form, which is the direction
that cannot be verified by the modules it guards (they are all clean, so they
exercise only the negative case).
"""

from __future__ import annotations

import ast
import types

import pytest
from _read_only_guard import (
    READ_ONLY_HEADER,
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)


def _module_from_source(tmp_path, source: str) -> types.ModuleType:
    """Build a module object whose on-disk source is ``source``.

    The guard reads its subject off disk via ``inspect.getsourcefile``, so a fake
    needs a real file — it is never imported or executed, only parsed.
    """
    path = tmp_path / "subject.py"
    path.write_text(source, encoding="utf-8")
    module = types.ModuleType("subject")
    module.__file__ = str(path)
    return module


@pytest.mark.parametrize(
    "call",
    [
        pytest.param('path.write_text("x")', id="write_text"),
        pytest.param('path.write_bytes(b"x")', id="write_bytes"),
        pytest.param("path.touch()", id="touch"),
        pytest.param("path.mkdir()", id="mkdir"),
        pytest.param("path.unlink()", id="unlink"),
        pytest.param('path.rename("other")', id="rename"),
        pytest.param('os.replace(path, "other")', id="replace"),
        pytest.param("os.makedirs(path)", id="makedirs"),
        pytest.param("os.remove(path)", id="remove"),
        pytest.param("path.rmdir()", id="rmdir"),
    ],
)
def test_the_guard_fires_on_a_mutating_attribute_call(tmp_path, call: str) -> None:
    module = _module_from_source(tmp_path, f"def f(path, os):\n    {call}\n")

    with pytest.raises(AssertionError, match="must be read-only"):
        assert_module_is_read_only(module)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param('open(path, "w")', id="builtin-positional"),
        pytest.param('open(path, mode="a")', id="builtin-keyword"),
        pytest.param('open(path, "r+")', id="builtin-update"),
        pytest.param('open(path, "xb")', id="builtin-exclusive"),
        # The forms the guard used to miss entirely: an attribute call named ``open``
        # matched neither the forbidden-attribute set nor the builtin branch.
        pytest.param('path.open("w")', id="path-open-positional"),
        pytest.param('path.open(mode="a")', id="path-open-keyword"),
        pytest.param('path.open("r+b")', id="path-open-update"),
    ],
)
def test_the_guard_fires_on_a_mutating_open(tmp_path, call: str) -> None:
    module = _module_from_source(tmp_path, f"def f(path):\n    with {call} as h:\n        h\n")

    with pytest.raises(AssertionError, match="must be read-only"):
        assert_module_is_read_only(module)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("os.open(path, os.O_WRONLY)", id="wronly"),
        pytest.param("os.open(path, os.O_RDWR)", id="rdwr"),
        pytest.param("os.open(path, os.O_RDONLY | os.O_CREAT)", id="creat-in-mask"),
        pytest.param("os.open(path, os.O_RDONLY | os.O_TRUNC)", id="trunc-in-mask"),
        pytest.param("os.open(path, O_APPEND)", id="bare-name"),
    ],
)
def test_the_guard_fires_on_a_mutating_os_open_flag(tmp_path, call: str) -> None:
    # ``os.open`` takes an integer flag mask, not a mode string, so the mode-character
    # check cannot see it. A read-only module opening by descriptor would otherwise be
    # wholly unguarded — which matters now that ``file_adapter.runs`` does exactly that.
    module = _module_from_source(tmp_path, f"def f(path, os, O_APPEND):\n    {call}\n")

    with pytest.raises(AssertionError, match="must be read-only"):
        assert_module_is_read_only(module)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('def f(path):\n    return path.read_bytes()\n', id="read_bytes"),
        pytest.param('def f(path):\n    return path.read_text()\n', id="read_text"),
        pytest.param('def f(path):\n    return path.stat()\n', id="stat"),
        pytest.param('def f(path):\n    with open(path) as h:\n        return h.read()\n', id="builtin-default-mode"),
        pytest.param('def f(path):\n    with open(path, "rb") as h:\n        return h.read()\n', id="builtin-rb"),
        pytest.param('def f(path):\n    with path.open("rb") as h:\n        return h.read()\n', id="path-open-rb"),
        pytest.param(
            "def f(path, os):\n    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)\n",
            id="os-open-readonly",
        ),
    ],
)
def test_the_guard_stays_silent_on_a_read_only_call(tmp_path, source: str) -> None:
    # The other half: a guard that fired on ordinary reads would be worked around
    # rather than fixed, so the read forms these modules actually use must stay clean.
    assert_module_is_read_only(_module_from_source(tmp_path, source))


def test_the_header_guard_fires_when_the_header_is_missing(tmp_path) -> None:
    module = _module_from_source(tmp_path, '"""No header here."""\n')

    with pytest.raises(AssertionError, match="READ-ONLY header"):
        assert_module_carries_read_only_header(module)


def test_the_header_guard_accepts_the_literal_header(tmp_path) -> None:
    module = _module_from_source(tmp_path, f'{READ_ONLY_HEADER}\n"""Doc."""\n')

    assert_module_carries_read_only_header(module)
