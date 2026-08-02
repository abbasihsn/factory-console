"""Shared AST guards for the codebase's read-only modules.

Several adapters (``file_adapter.run_state``, ``file_adapter.watcher_real``)
declare themselves read-only: they observe the target project but MUST NOT
write, create, or delete under it. Each carries a literal ``READ-ONLY`` header
and is pinned by a test asserting its source contains no filesystem-mutating
call. That guard used to be copy-pasted per module, so extending it (e.g. adding
``os.replace`` / ``shutil.move`` to the forbidden set) had to be hand-synced
across copies or it silently weakened one module. It lives here once instead;
each read-only module's test calls these with its module under test.

This is a test helper, not a test module — the leading underscore keeps pytest
from collecting it, and it imports as a top-level module via the ``tests`` entry
in ``[tool.pytest.ini_options].pythonpath``.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

# Attribute-call names that mutate the filesystem (Path or os/shutil methods).
_FORBIDDEN_ATTR_CALLS = frozenset(
    {
        "write_text",
        "write_bytes",
        "touch",
        "mkdir",
        "rmdir",
        "unlink",
        "rename",
        "replace",
        "makedirs",
        "remove",
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


def _mode_requests_mutation(mode: ast.expr | None) -> bool:
    """Does this ``mode`` argument node request writing, creation or truncation?"""
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and bool(set(mode.value) & _FORBIDDEN_OPEN_MODE_CHARS)
    )


def _forbidden_open_flags(call: ast.Call) -> list[str]:
    """Return the mutating ``os.open`` flag names appearing anywhere in ``call``.

    The flags argument is an OR-mask expression (``os.O_RDONLY | os.O_NOFOLLOW``), so
    rather than evaluate it, walk it and collect every ``O_*`` name mentioned. A
    read-only module has no reason to NAME a write flag at all, so mentioning one is
    itself the violation — and this cannot be fooled by how the mask is spelled.
    """
    return sorted(
        {
            node.attr if isinstance(node, ast.Attribute) else node.id
            for node in ast.walk(call)
            if (isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_OPEN_FLAGS)
            or (isinstance(node, ast.Name) and node.id in _FORBIDDEN_OPEN_FLAGS)
        }
    )


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
        elif isinstance(func, ast.Attribute) and func.attr == "open":
            # ``path.open(...)`` / ``os.open(...)``, the BOUND form. Checked as well as
            # the builtin below, not instead of it: ``file_adapter.runs`` reads through
            # ``Path.open``/``os.open`` rather than ``read_bytes`` so it can bound the
            # read, and an attribute call named ``open`` matches neither
            # ``_FORBIDDEN_ATTR_CALLS`` nor the ``ast.Name`` branch — so a later edit to
            # ``path.open("w")`` in a READ-ONLY module used to pass this guard green.
            mode = _open_mode_arg(node, positional_index=0)
            if _mode_requests_mutation(mode):
                violations.append(f"open(mode={mode.value!r}) at line {node.lineno}")  # type: ignore[attr-defined]
            for flag in _forbidden_open_flags(node):
                violations.append(f"open(..., {flag}) at line {node.lineno}")
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = _open_mode_arg(node, positional_index=1)
            if _mode_requests_mutation(mode):
                violations.append(f"open(mode={mode.value!r}) at line {node.lineno}")  # type: ignore[attr-defined]
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
