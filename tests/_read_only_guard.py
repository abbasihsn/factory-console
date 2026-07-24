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

# The literal header every read-only module must carry.
READ_ONLY_HEADER = "# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests."


def _module_source(module: ModuleType) -> str:
    """Return the on-disk source text of ``module``."""
    source_file = inspect.getsourcefile(module)
    assert source_file is not None, f"could not locate {module.__name__} source on disk"
    return Path(source_file).read_text()


def _open_mode_arg(call: ast.Call) -> ast.expr | None:
    """Return the ``mode`` argument node of an ``open(...)`` call, if given."""
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    return None


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
        elif isinstance(func, ast.Name) and func.id == "open":
            mode = _open_mode_arg(node)
            if (
                isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and set(mode.value) & _FORBIDDEN_OPEN_MODE_CHARS
            ):
                violations.append(f"open(mode={mode.value!r}) at line {node.lineno}")
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
