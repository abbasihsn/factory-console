"""Tests for the shared read-only AST guard itself.

The guard in ``tests/_read_only_guard.py`` is what pins the READ-ONLY contract on
every module that calls it — grep for ``assert_module_is_read_only`` for the
current adopters, which is the pointer that module gives in place of a list, and
for the reason it gives: an enumeration here "was already stale by three modules",
and "a maintainer reading a stale list as exhaustive concludes a module is covered
when nothing checks it". This docstring carried exactly such a list, and it went
stale again the moment ``file_adapter.run_artifacts`` adopted the guard.

The guard had no tests of its own, and its failure mode is SILENCE: a
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
        pytest.param('os.rename(path, "other")', id="os-rename"),
        pytest.param('os.replace(path, "other")', id="replace"),
        # ``Path.replace(target)`` is a rename, and it is the spelling this pathlib-first
        # codebase would actually reach for. Its receiver is an ordinary local, so the
        # receiver rule that stops ``status.replace("_", "-")`` cannot admit it — ARITY
        # separates them instead: the path form takes exactly one argument, the string
        # form at least two.
        pytest.param('path.replace("other")', id="path-replace"),
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
        pytest.param("shutil.copyfileobj(other, path)", id="shutil-copyfileobj"),
        pytest.param("shutil.copy2(other, path)", id="shutil-copy2"),
        # Creation and metadata mutation: no bytes written, but each alters something
        # under the observed project, which the READ-ONLY header forbids just as much.
        pytest.param("os.symlink(other, path)", id="symlink"),
        pytest.param("os.link(other, path)", id="link"),
        pytest.param("os.mkfifo(path)", id="mkfifo"),
        pytest.param("os.mknod(path)", id="mknod"),
        pytest.param("os.truncate(path, 0)", id="truncate"),
        pytest.param("os.chmod(path, 0o600)", id="chmod"),
        pytest.param("os.chown(path, 0, 0)", id="chown"),
        pytest.param("os.utime(path)", id="utime"),
        # The rename/remove pairs' plural spellings. They were added to the forbidden
        # set alongside their singulars but, unlike them, never exercised — and an
        # untested name in this set is indistinguishable from an absent one, because
        # the guard's failure mode is silence.
        pytest.param("os.renames(path, other)", id="renames"),
        pytest.param("os.removedirs(path)", id="removedirs"),
        # The PATHLIB spelling of ``os.symlink``/``os.link``. This codebase is
        # pathlib-first, so it is the spelling a read-only module would actually reach
        # for — and it was the one the set did not cover.
        pytest.param("path.symlink_to(other)", id="path-symlink_to"),
        pytest.param("path.hardlink_to(other)", id="path-hardlink_to"),
        # Descriptor-level mutation. ``file_adapter.runs`` is the first guarded module to
        # hold a raw descriptor, and these are the only calls that can mutate through
        # one; their path-level twins were already forbidden.
        pytest.param("os.ftruncate(fd, 0)", id="ftruncate"),
        pytest.param("os.fchmod(fd, 0o600)", id="fchmod"),
        pytest.param("os.fchown(fd, 0, 0)", id="fchown"),
        pytest.param('os.pwrite(fd, b"x", 0)', id="pwrite"),
        pytest.param('os.writev(fd, [b"x"])', id="writev"),
    ],
)
def test_the_guard_fires_on_a_mutating_attribute_call(tmp_path, call: str) -> None:
    module = _module_from_source(tmp_path, f"def f(path, other, fd, os, shutil):\n    {call}\n")

    with pytest.raises(AssertionError, match="must be read-only"):
        assert_module_is_read_only(module)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("rmtree(path)", id="from-shutil-import-rmtree"),
        pytest.param("move(path, other)", id="from-shutil-import-move"),
        pytest.param("remove(path)", id="from-os-import-remove"),
        pytest.param("makedirs(path)", id="from-os-import-makedirs"),
        pytest.param("symlink(other, path)", id="from-os-import-symlink"),
        pytest.param("chmod(path, 0o600)", id="from-os-import-chmod"),
        pytest.param("copytree(other, path)", id="from-shutil-import-copytree"),
        pytest.param("ftruncate(fd, 0)", id="from-os-import-ftruncate"),
        pytest.param('pwrite(fd, b"x", 0)', id="from-os-import-pwrite"),
        pytest.param("fchmod(fd, 0o600)", id="from-os-import-fchmod"),
        pytest.param("fchown(fd, 0, 0)", id="from-os-import-fchown"),
        pytest.param('writev(fd, [b"x"])', id="from-os-import-writev"),
        pytest.param("copyfileobj(other, path)", id="from-shutil-import-copyfileobj"),
        pytest.param("copy2(other, path)", id="from-shutil-import-copy2"),
        pytest.param("symlink_to(other)", id="from-pathlib-import-symlink_to"),
        pytest.param("hardlink_to(other)", id="from-pathlib-import-hardlink_to"),
        pytest.param("link(other, path)", id="from-os-import-link"),
        pytest.param("mknod(path)", id="from-os-import-mknod"),
        pytest.param("chown(path, 0, 0)", id="from-os-import-chown"),
        pytest.param("utime(path)", id="from-os-import-utime"),
        pytest.param("renames(path, other)", id="from-os-import-renames"),
        pytest.param("removedirs(path)", id="from-os-import-removedirs"),
    ],
)
def test_the_guard_fires_on_a_mutating_call_imported_by_name(tmp_path, call: str) -> None:
    # The spelling an ``from shutil import rmtree`` gives: an ``ast.Name`` call, which
    # the forbidden-attribute set used to be matched against never. The whole set was
    # therefore one import statement away from contributing nothing, and every name the
    # widening added — ``rmtree`` and ``move`` among them — was uncovered in this form.
    module = _module_from_source(tmp_path, f"def f(path, other, fd):\n    {call}\n")

    with pytest.raises(AssertionError, match="must be read-only"):
        assert_module_is_read_only(module)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "from shutil import rmtree as _rm\n\n\ndef f(path):\n    return _rm(path)\n",
            id="aliased-shutil-rmtree",
        ),
        pytest.param(
            "from os import remove as _unlink\n\n\ndef f(path):\n    return _unlink(path)\n",
            id="aliased-os-remove",
        ),
        # The aliased FLAG, which defeats the flag-name scan the same way: the mask names
        # ``_C``, and ``_C`` is not an ``O_`` name.
        pytest.param(
            "from os import O_CREAT as _C\n\n\ndef f(path, os):\n"
            "    return os.open(path, os.O_RDONLY | _C)\n",
            id="aliased-os-O_CREAT",
        ),
        # The import alone, with no call through it at all — the import IS the violation,
        # which is what also covers an alias dispatched through a form no name-matcher
        # sees.
        pytest.param(
            "from shutil import move as _mv\n\n\ndef f(path):\n    return path\n",
            id="aliased-import-never-called",
        ),
        # The aliased MODULE, which is a different bypass from the aliased function above
        # and the one that qualifying the collision-prone names by receiver would
        # otherwise have opened: ``sh.move`` is ``shutil.move`` under another name, and a
        # receiver rule that knew only the literal spellings would have let it through —
        # a name the receiver-free matching it replaced had caught.
        pytest.param(
            "import shutil as sh\n\n\ndef f(a, b):\n    return sh.move(a, b)\n",
            id="aliased-shutil-module-move",
        ),
        pytest.param(
            "import os as _os\n\n\ndef f(path):\n    return _os.remove(path)\n",
            id="aliased-os-module-remove",
        ),
        pytest.param(
            "import os as _os\n\n\ndef f(path):\n    return _os.truncate(path, 0)\n",
            id="aliased-os-module-truncate",
        ),
    ],
)
def test_the_guard_fires_on_a_mutating_name_imported_under_an_alias(tmp_path, source: str) -> None:
    # The bypass one ``as`` clause beyond the bare-name spelling above. A call node cannot
    # see the import that bound its name, so matching the name as written finds ``_rm``
    # and stops — exactly the "one import statement away from contributing nothing" hole
    # the bare-name branch was added to close, reopened by renaming.
    with pytest.raises(AssertionError, match="must be read-only"):
        assert_module_is_read_only(_module_from_source(tmp_path, source))


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
        # Receivers the mode-position whitelist did not list. It defaulted anything
        # unlisted to argument 0, which on these is the path or the descriptor — so the
        # real mode at argument 1 was never inspected and a write open passed green.
        pytest.param('tarfile.open(path, "w")', id="tarfile-open-write"),
        pytest.param('_os.fdopen(fd, "wb")', id="aliased-os-fdopen-write"),
        pytest.param('zipfile.ZipFile.open(path, "w")', id="dotted-receiver-open-write"),
        # The bare-name spelling of the wrappers, for the same reason the mutating calls
        # above are checked in both spellings.
        pytest.param('fdopen(fd, "wb")', id="from-os-import-fdopen-write"),
    ],
)
def test_the_guard_fires_on_a_mutating_open(tmp_path, call: str) -> None:
    module = _module_from_source(
        tmp_path,
        "def f(path, fd, os, _os, io, gzip, tarfile, zipfile):\n"
        f"    with {call} as h:\n        h\n",
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
        # The mask spelled as a NUMBER, which names no flag at all and so was invisible
        # to a scan that matches flag NAMES. 577 is ``O_RDONLY | O_CREAT | O_EXCL`` on
        # Linux — a writable descriptor obtained without writing a single ``O_`` name,
        # after which ``os.write(fd, ...)`` completes the mutation under a name too
        # generic to forbid receiver-free. The mask is refused rather than decoded, so
        # the guard does not have to track any platform's flag values.
        pytest.param("os.open(path, 577)", id="numeric-flag-mask"),
        pytest.param("os.open(path, 0o1101)", id="octal-flag-mask"),
        # And its KEYWORD spelling, for the same reason every call name here is checked
        # in both spellings: which one appears is the caller's choice, and a
        # positional-only check left the easier one to write by accident uncovered.
        pytest.param("os.open(path, flags=577)", id="numeric-flag-mask-keyword"),
        # A LITERAL path carrying the mask. Excluding the bound ``Path.open(mode,
        # buffering)`` form on "argument 0 is a string" rather than on "argument 0 is
        # MODE-SHAPED" let this straight through — the one form the numeric check exists
        # to stop, defeated by spelling the path inline.
        pytest.param('os.open("/target/file.txt", 577)', id="numeric-flag-mask-literal-path"),
        # The numeric mask HOISTED into a local, which is the combination of the two
        # spellings above and used to escape both checks at once: the call node shows only
        # the name ``flags`` to the numeric check, while the flag-NAME scan finds no name
        # because a bare octal spells none. It is one line from the shape
        # ``file_adapter.runs`` itself uses, and it passed green.
        pytest.param(
            "flags = 0o1101\n    os.open(path, flags)",
            id="numeric-mask-hoisted-into-a-local",
        ),
        pytest.param(
            "flags = 577\n    os.open(path, flags=flags)",
            id="numeric-mask-hoisted-into-a-local-keyword",
        ),
        # A number OR-ed in beside a named flag: the named half is read-only, so the flag
        # scan is satisfied, and the mask is a ``BinOp`` rather than a bare literal, so
        # reading the argument as a constant saw nothing.
        pytest.param(
            "os.open(path, os.O_RDONLY | 0o100)",
            id="numeric-mask-or-ed-beside-a-named-flag",
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
        # The other half of dropping the receiver whitelist: an UNLISTED receiver must
        # not have its path literal read as a mode either. A mode is recognised by its
        # shape — drawn wholly from the mode alphabet — so a path is declined at
        # whichever argument it is offered at, without anyone naming the receiver.
        pytest.param(
            'def f(tarfile):\n    return tarfile.open(".factory/last-stop.json")\n',
            id="unlisted-receiver-open-path-literal-containing-mode-chars",
        ),
        pytest.param(
            'def f(archive):\n    return archive.open("a/w+x.json", "rb")\n',
            id="unlisted-receiver-open-read-mode-second",
        ),
        # The residual the mode-SHAPE check alone does not settle: a path drawn wholly
        # from the mode alphabet. Argument 1 is a string, so it is the mode and argument
        # 0 is never offered — which is why the position is derived from the arguments
        # rather than each of them being tried in turn.
        pytest.param(
            'def f(gzip):\n    return gzip.open("w", "r")\n',
            id="path-literal-that-is-itself-mode-shaped",
        ),
        # And its bound counterpart, where argument 1 is the buffering int rather than a
        # mode, so argument 0 is correctly read as the mode and is read-only. This is
        # also what keeps the numeric-flag-mask rule off the bound form: its argument 1
        # is an int BY DESIGN, and a string constant at argument 0 is what tells the two
        # apart.
        pytest.param(
            'def f(path):\n    return path.open("rb", 8192)\n',
            id="bound-open-with-buffering",
        ),
        # A NAMED read-only mask still passes: the numeric rule refuses the spelling that
        # names nothing, not the act of passing flags.
        pytest.param(
            "def f(path, os):\n    return os.open(path, os.O_RDONLY | os.O_CLOEXEC)\n",
            id="os-open-named-readonly-mask",
        ),
        # Argument 0 of a BARE ``open`` is the file, always — so a filename that happens
        # to be spelled out of mode characters is still a filename, and reading it as a
        # mode would fail a plain read.
        pytest.param('def f():\n    return open("wax")\n', id="builtin-open-mode-shaped-filename"),
        # The collision the forbidden set's own exclusion rule forbids, which five of its
        # members broke anyway. Matched receiver-free, these ordinary reads reported as
        # filesystem mutations — ``status.replace("_", "-")`` as ``replace()`` and
        # ``candidates.remove(x)`` as ``remove()`` — which is the false fire this guard's
        # docstring calls the failure that gets it worked around rather than fixed.
        pytest.param(
            'def f(status):\n    return status.replace("_", "-")\n',
            id="str-replace",
        ),
        pytest.param("def f(candidates, x):\n    candidates.remove(x)\n", id="list-remove"),
        pytest.param(
            "def f(model):\n    return dataclasses.replace(model, a=1)\n",
            id="dataclasses-replace",
        ),
        pytest.param("def f(queue, item):\n    return queue.move(item)\n", id="non-fs-move"),
        pytest.param("def f(report):\n    return report.truncate()\n", id="non-fs-truncate"),
        # ``rename`` collides the same way, and is separated by ARITY rather than by
        # receiver: the dataframe spelling passes its columns as a KEYWORD, while
        # ``path.rename(other)`` — which must still fire, and does, above — takes exactly
        # one positional argument.
        pytest.param(
            "def f(frame):\n    return frame.rename(columns={'a': 'b'})\n",
            id="non-fs-rename-by-keyword",
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
