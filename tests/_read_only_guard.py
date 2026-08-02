"""Shared AST guards for the codebase's read-only modules.

Several adapters declare themselves read-only: they observe the target project
but MUST NOT write, create, or delete under it. Each carries a literal
``READ-ONLY`` header and is pinned by a test asserting its source contains no
filesystem-mutating call. That guard used to be copy-pasted per module, so
extending it (e.g. adding ``os.replace`` / ``shutil.move`` to the forbidden set)
had to be hand-synced across copies or it silently weakened one module. It lives
here once instead; each read-only module's test calls these with its module under
test.

Deliberately NOT an enumeration of those adapters. An earlier revision named two
of them, and the list was already stale by three modules — the same drift
``file_adapter.path_safety`` refuses to reintroduce for the ticket-id rule, and
for the same reason: a maintainer reading a stale list as exhaustive concludes a
module is covered when nothing checks it. Grep for ``assert_module_is_read_only``
for the current adopters.

This is a test helper, not a test module — the leading underscore keeps pytest
from collecting it, and it imports as a top-level module via the ``tests`` entry
in ``[tool.pytest.ini_options].pythonpath``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType
from typing import TypeGuard

# Attribute-call names that mutate the filesystem (Path or os/shutil methods).
#
# Names are matched WITHOUT their receiver, so a name that is also an ordinary
# non-filesystem method belongs elsewhere or nowhere: bare ``copy`` is excluded for
# exactly that reason (``dict.copy()``, ``BaseModel.model_copy()``), while
# ``copyfile``/``copy2``/``copytree`` name only the shutil functions and are safe to
# list. Erring toward a name that cannot collide keeps the guard from firing on a
# read, which its own tests treat as the failure that gets it worked around.
_FORBIDDEN_ATTR_CALLS = frozenset(
    {
        "write_text",
        "write_bytes",
        "touch",
        "mkdir",
        "rmdir",
        "unlink",
        "rename",
        "renames",
        "replace",
        "makedirs",
        "remove",
        "removedirs",
        # shutil's mutating surface. The module docstring cites ``shutil.move`` as the
        # example of what belongs here, so its absence read as coverage that was never
        # there — ``shutil.rmtree`` in a READ-ONLY module used to pass this guard green.
        "move",
        "rmtree",
        "copyfile",
        "copy2",
        "copytree",
        # os-level creation and metadata mutation. None of these writes bytes, but each
        # creates or alters something under the observed project, which the READ-ONLY
        # header forbids just as squarely.
        "symlink",
        "link",
        "mknod",
        "mkfifo",
        "truncate",
        "chmod",
        "chown",
        "utime",
    }
)
# open() mode characters that request writing/creation/truncation.
_FORBIDDEN_OPEN_MODE_CHARS = frozenset("wax+")
# ``os.open`` flag names that request writing/creation/truncation. ``os.open`` takes
# an integer flag mask, not a mode string, so the character check above cannot see it:
# a read-only module opening by descriptor (as ``file_adapter.runs`` does, to gate on
# the OPENED inode rather than on a name) would otherwise be unguarded entirely.
_FORBIDDEN_OPEN_FLAGS = frozenset(
    {"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_EXCL"}
)
# Attribute calls that open a path or a descriptor and take a MODE STRING. ``fdopen``
# is here because a read-only module that opens by descriptor still has to wrap it to
# read it — ``file_adapter.runs`` does exactly that — and ``os.fdopen(fd, "wb")``
# matches neither the forbidden-attribute set nor the plain ``open`` name, so it used
# to be invisible to every branch of this guard.
_OPEN_ATTR_NAMES = frozenset({"open", "fdopen"})
# Receivers whose ``open``/``fdopen`` takes the path or descriptor FIRST and the mode
# SECOND (``os.fdopen(fd, "w")``, ``gzip.open(p, "wb")``), unlike the bound
# ``Path.open(mode)``, which is already carrying its path. ``os`` is included so
# ``os.open(path, flags)`` reads its argument 0 as the PATH it is: index 0 there is not
# a mode, and treating it as one made any literal path containing ``w``, ``a``, ``x`` or
# ``+`` — ``.factory/...`` among them — a false READ-ONLY violation on a plain read.
_MODE_SECOND_OPEN_RECEIVERS = frozenset({"os", "io", "gzip", "bz2", "lzma", "codecs"})

# The literal header every read-only module must carry.
READ_ONLY_HEADER = "# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests."


def _module_source(module: ModuleType) -> str:
    """Return the on-disk source text of ``module``."""
    source_file = inspect.getsourcefile(module)
    assert source_file is not None, f"could not locate {module.__name__} source on disk"
    return Path(source_file).read_text()


def _open_mode_arg(call: ast.Call, *, positional_index: int) -> ast.expr | None:
    """Return the ``mode`` argument node of an ``open(...)`` call, if given.

    ``positional_index`` differs by call form and must be passed explicitly: the
    builtin is ``open(file, mode)`` (index 1) while ``Path.open(mode)`` is already
    bound to its path (index 0). Reading index 1 for both would take ``buffering``
    for the mode of a ``Path.open`` call, which is never a string constant — so the
    mode check would silently never fire.
    """
    if len(call.args) > positional_index:
        return call.args[positional_index]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


def _mode_requests_mutation(mode: ast.expr | None) -> TypeGuard[ast.Constant]:
    """Does this ``mode`` argument node request writing, creation or truncation?

    Returns a :class:`TypeGuard` rather than a plain ``bool`` so the ``isinstance``
    narrowing survives the call: a caller guarded by this may read ``mode.value`` to
    build its message. A plain ``bool`` discards that at the return boundary, and the
    two call sites then need a blanket ``# type: ignore[attr-defined]`` each — which
    would equally swallow a genuine attribute typo in the very guard that pins a
    security property.
    """
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and bool(set(mode.value) & _FORBIDDEN_OPEN_MODE_CHARS)
    )


def _is_getattr_call(node: ast.AST) -> TypeGuard[ast.Call]:
    """Is ``node`` a call to the builtin ``getattr``?"""
    if not isinstance(node, ast.Call):
        return False
    return isinstance(node.func, ast.Name) and node.func.id == "getattr"


def _forbidden_open_flags(tree: ast.AST) -> list[str]:
    """Return every mutating ``os.open`` flag NAMED anywhere under ``tree``.

    Scanned at MODULE scope, not per call, because a mask is normally assembled before
    it is passed. ``file_adapter.runs`` writes ``flags = os.O_RDONLY | ...`` on one line
    and ``os.open(path, flags)`` on the next, so walking the CALL node sees only the
    local name ``flags`` and every forbidden flag OR-ed into it is invisible — the check
    contributed exactly zero coverage for the one module it was added for, and
    ``flags = os.O_RDWR | os.O_CREAT`` there passed green. A read-only module has no
    reason to NAME a write flag ANYWHERE, so the mention itself is the violation, and
    stating it that way makes the check independent of where the mask is built.

    Three spellings are recognised: the attribute (``os.O_CREAT``), the bare name
    (``O_CREAT``, imported ``from os``), and the string handed to ``getattr``
    (``getattr(os, "O_CREAT", 0)``) — the last because that is this codebase's own idiom
    for a flag that may be absent on some platform, and it is a string constant no
    name-matcher can see. ONLY the ``getattr`` argument is matched as a string, never
    any string constant: a docstring explaining why the module does not use ``O_TRUNC``
    must not fail the guard.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_OPEN_FLAGS:
            found.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_OPEN_FLAGS:
            found.add(node.id)
        elif _is_getattr_call(node):
            for argument in node.args[1:2]:
                if isinstance(argument, ast.Constant) and argument.value in _FORBIDDEN_OPEN_FLAGS:
                    found.add(argument.value)
    return sorted(found)


def assert_module_is_read_only(module: ModuleType) -> None:
    """Assert ``module``'s source contains no filesystem-mutating call."""
    tree = ast.parse(_module_source(module))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTR_CALLS:
            violations.append(f"{func.attr}() at line {node.lineno}")
        elif isinstance(func, ast.Attribute) and func.attr in _OPEN_ATTR_NAMES:
            # ``path.open(...)`` / ``os.open(...)`` / ``os.fdopen(...)``, the BOUND
            # forms. Checked as well as the builtin below, not instead of it:
            # ``file_adapter.runs`` reads through ``os.open`` + ``os.fdopen`` rather than
            # ``read_bytes`` so it can bound the read, and an attribute call named
            # ``open`` matches neither ``_FORBIDDEN_ATTR_CALLS`` nor the ``ast.Name``
            # branch — so a later edit to ``path.open("w")`` in a READ-ONLY module used
            # to pass this guard green.
            #
            # WHERE the mode sits depends on the receiver, so it is looked up rather
            # than assumed: ``Path.open(mode)`` is already bound to its path (index 0),
            # while ``os.fdopen(fd, mode)`` and ``gzip.open(path, mode)`` put it second.
            # Assuming index 0 for all of them read ``os.open``'s PATH as a mode string,
            # which fails a plain read whose path literal happens to contain ``w``,
            # ``a``, ``x`` or ``+``, and skipped the mode of every wrapper that puts it
            # second.
            receiver = func.value.id if isinstance(func.value, ast.Name) else None
            index = 1 if receiver in _MODE_SECOND_OPEN_RECEIVERS else 0
            mode = _open_mode_arg(node, positional_index=index)
            if _mode_requests_mutation(mode):
                violations.append(f"{func.attr}(mode={mode.value!r}) at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = _open_mode_arg(node, positional_index=1)
            if _mode_requests_mutation(mode):
                violations.append(f"open(mode={mode.value!r}) at line {node.lineno}")

    # Flags are collected over the WHOLE module rather than per call — see
    # :func:`_forbidden_open_flags` for why a per-call walk could not see the mask
    # ``file_adapter.runs`` actually builds.
    violations.extend(
        f"names the mutating open flag {flag}" for flag in _forbidden_open_flags(tree)
    )

    module_name = Path(inspect.getsourcefile(module) or module.__name__).name
    assert not violations, (
        f"{module_name} must be read-only but contains mutation calls: " + ", ".join(violations)
    )


def assert_module_carries_read_only_header(module: ModuleType) -> None:
    """Assert ``module``'s source carries the literal READ-ONLY header comment."""
    module_name = Path(inspect.getsourcefile(module) or module.__name__).name
    assert READ_ONLY_HEADER in _module_source(module), (
        f"{module_name} must carry the literal READ-ONLY header comment"
    )
