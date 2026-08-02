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
        # shutil's mutating surface. ``shutil.move`` is the example the guard's own
        # docstring gives for what belongs in the forbidden set, so its absence read as
        # coverage that was never there.
        pytest.param("shutil.move(path, other)", id="shutil-move"),
        pytest.param("shutil.rmtree(path)", id="shutil-rmtree"),
        pytest.param("shutil.copyfile(other, path)", id="shutil-copyfile"),
        pytest.param("shutil.copytree(other, path)", id="shutil-copytree"),
        # Creation and metadata mutation: no bytes written, but each alters something
        # under the observed project, which the READ-ONLY header forbids just as much.
        pytest.param("os.symlink(other, path)", id="symlink"),
        pytest.param("os.mkfifo(path)", id="mkfifo"),
        pytest.param("os.truncate(path, 0)", id="truncate"),
        pytest.param("os.chmod(path, 0o600)", id="chmod"),
    ],
)
def test_the_guard_fires_on_a_mutating_attribute_call(tmp_path, call: str) -> None:
    module = _module_from_source(tmp_path, f"def f(path, other, os, shutil):\n    {call}\n")

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
        # ``os.fdopen`` is how a module that opened by DESCRIPTOR wraps it to read —
        # exactly what ``file_adapter.runs`` does. It is neither a forbidden attribute
        # name nor the builtin ``open``, so it used to match no branch at all and
        # ``os.fdopen(fd, "wb")`` passed green.
        pytest.param('os.fdopen(fd, "wb")', id="os-fdopen-write"),
        pytest.param('os.fdopen(fd, mode="a")', id="os-fdopen-append-keyword"),
        # Wrappers that put the mode SECOND, where reading argument 0 as the mode saw
        # the path instead and never inspected the real mode.
        pytest.param('gzip.open(path, "wb")', id="gzip-open-write"),
        pytest.param('io.open(path, "w")', id="io-open-write"),
    ],
)
def test_the_guard_fires_on_a_mutating_open(tmp_path, call: str) -> None:
    module = _module_from_source(
        tmp_path, f"def f(path, fd, os, io, gzip):\n    with {call} as h:\n        h\n"
    )

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
        # The spelling ``file_adapter.runs`` ACTUALLY uses: the mask is assembled into a
        # local first, so a walk of the ``os.open`` call node sees only the name
        # ``flags``. Scanning per call meant the flag check contributed zero coverage for
        # the one module it was written for, and this exact source passed green.
        pytest.param(
            "flags = os.O_RDWR | os.O_CREAT\n    os.open(path, flags)",
            id="mask-hoisted-into-a-local",
        ),
        pytest.param(
            "flags = os.O_RDONLY | os.O_TRUNC\n    os.open(path, flags)",
            id="trunc-in-a-hoisted-mask",
        ),
        # That module's own portability idiom for a flag that may be absent: the flag is
        # named as a STRING, which no name-matcher can see.
        pytest.param(
            'os.open(path, os.O_RDONLY | getattr(os, "O_CREAT", 0))',
            id="flag-named-via-getattr",
        ),
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
        pytest.param("def f(path):\n    return path.read_bytes()\n", id="read_bytes"),
        pytest.param("def f(path):\n    return path.read_text()\n", id="read_text"),
        pytest.param("def f(path):\n    return path.stat()\n", id="stat"),
        pytest.param(
            "def f(path):\n    with open(path) as h:\n        return h.read()\n",
            id="builtin-default-mode",
        ),
        pytest.param(
            'def f(path):\n    with open(path, "rb") as h:\n        return h.read()\n',
            id="builtin-rb",
        ),
        pytest.param(
            'def f(path):\n    with path.open("rb") as h:\n        return h.read()\n',
            id="path-open-rb",
        ),
        pytest.param(
            "def f(path, os):\n    return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)\n",
            id="os-open-readonly",
        ),
        pytest.param(
            'def f(fd, os):\n    return os.fdopen(fd, "rb", closefd=False)\n',
            id="os-fdopen-rb",
        ),
        # ``os.open``'s argument 0 is the PATH, not a mode. Reading it as one failed a
        # plain read whose path literal contains ``w``, ``a``, ``x`` or ``+`` — and
        # ``.factory``, which every artifact path in this codebase starts with, contains
        # an ``a``.
        pytest.param(
            'def f(os):\n    return os.open(".factory/last-stop.json", os.O_RDONLY)\n',
            id="os-open-path-literal-containing-mode-chars",
        ),
        # A docstring may DISCUSS a write flag; only naming one as a flag is the
        # violation, or the guard would fire on the comment explaining why it does not
        # write.
        pytest.param(
            'def f(path):\n    """Never passes O_CREAT or O_TRUNC."""\n    return path\n',
            id="write-flag-named-only-in-a-docstring",
        ),
        # A non-filesystem ``copy``: bare ``copy`` is deliberately out of the forbidden
        # set because ``dict.copy()``/``model_copy()`` would fire on a read.
        pytest.param("def f(data):\n    return data.copy()\n", id="dict-copy"),
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
