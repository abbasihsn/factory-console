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
        "copyfileobj",
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
        # The PATHLIB spellings of the two names above it, which this set otherwise pairs
        # without exception: ``mkdir``/``makedirs``, ``unlink``/``remove``,
        # ``rename``/``replace``, ``rmdir``/``removedirs``. ``symlink``/``link`` arrived
        # without their ``Path`` counterparts, and this codebase is pathlib-first — every
        # guarded module threads ``Path`` objects and the guard's own positive tests are
        # written as ``path.mkdir()`` — so the spelling a read-only module would actually
        # reach for was the one spelling not covered.
        "symlink_to",
        "hardlink_to",
        # DESCRIPTOR-level mutation, the half that was missing from a set listing only
        # path-level names. It is not hypothetical here: ``file_adapter.runs`` is the
        # first read-only module to hold a raw descriptor (``os.open`` + ``os.fdopen``,
        # so its gates apply to the opened inode rather than to a name), and the
        # path-level twins ``truncate``/``chmod``/``chown`` above were listed while their
        # ``f``-prefixed forms — the only ones that can act on that descriptor — were not.
        #
        # Bare ``write`` and ``writelines`` are deliberately NOT here, on the same
        # collision rule that excludes bare ``copy``: they name every file-like object's
        # method (``sys.stdout.write``, ``io.StringIO.write``), so listing them
        # receiver-free would fail a read. That leaves ``os.write(fd, ...)`` itself
        # uncovered by NAME — which is why the flag mask that produced a writable
        # descriptor is now a violation in its own right; see
        # :func:`_opens_with_an_opaque_flag_mask`.
        "pwrite",
        "writev",
        "ftruncate",
        "fchmod",
        "fchown",
    }
)
# open() mode characters that request writing/creation/truncation.
_FORBIDDEN_OPEN_MODE_CHARS = frozenset("wax+")
# Every character an open MODE string may contain. Used to tell a mode from a path
# without knowing which argument position the mode sits in — see
# :func:`_mode_requests_mutation`. A mode is drawn only from this alphabet, while any
# realistic path literal carries a separator, a dot or a letter outside it.
_OPEN_MODE_CHARS = frozenset("rwxabt+U")
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

# The literal header every read-only module must carry.
READ_ONLY_HEADER = "# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests."


def _module_source(module: ModuleType) -> str:
    """Return the on-disk source text of ``module``."""
    source_file = inspect.getsourcefile(module)
    assert source_file is not None, f"could not locate {module.__name__} source on disk"
    return Path(source_file).read_text()


def _open_mode_arg(call: ast.Call, *, bound: bool) -> ast.expr | None:
    """Return the ``mode`` argument node of an ``open(...)`` call, if given.

    WHERE the mode sits differs by call form, and it is DERIVED rather than guessed
    from the receiver's name. Three rules, in this order:

    1. an explicit ``mode=`` keyword is the mode, whatever the positional args hold.
       It comes first because ``open(path, mode="a")`` has a positional argument that
       is NOT the mode, and reading position 0 there would inspect the path;
    2. otherwise, a SECOND positional argument that is a string constant is the mode.
       In every real form — ``open(f, "w")``, ``gzip.open(p, "wb")``,
       ``os.fdopen(fd, "rb")``, ``tarfile.open(p, "w")`` — argument 1 being a string
       IS the mode; the one competing form, the bound ``Path.open(mode, buffering)``,
       has an int there and never a string;
    3. otherwise, and ONLY for a ``bound`` call, the first positional argument — the
       bound ``Path.open(mode)``, which is already carrying its path.

    ``bound`` says whether this was an ATTRIBUTE call (``path.open(...)``) rather than
    a bare name (``open(...)``), and rule 3 needs it. Argument 0 of a bare ``open`` is
    the FILE, always and by definition, so applying rule 3 to it reads a path as a
    mode — and a path may well be mode-shaped, since ``open("wax")`` names a file. The
    bound form is the only one where argument 0 can be a mode at all.

    The receiver used to pick the index off a closed whitelist of six modules, which
    was wrong in both directions: an unlisted receiver (``tarfile.open(path, "w")``, or
    ``os`` imported under an alias) fell back to index 0 and its real mode was never
    inspected, while a bound ``open`` on any unlisted receiver read a literal PATH as a
    mode. Deriving the position from the ARGUMENTS removes the guess without inheriting
    either failure.
    """
    for keyword in call.keywords:
        if keyword.arg == "mode":
            return keyword.value
    if len(call.args) >= 2 and _is_string_constant(call.args[1]):
        return call.args[1]
    if bound and call.args:
        return call.args[0]
    return None


def _is_string_constant(node: ast.expr) -> bool:
    """Is ``node`` a literal string? The test that tells a mode from a buffering int."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _mode_requests_mutation(mode: ast.expr | None) -> TypeGuard[ast.Constant]:
    """Does this ``mode`` argument node request writing, creation or truncation?

    Returns a :class:`TypeGuard` rather than a plain ``bool`` so the ``isinstance``
    narrowing survives the call: a caller guarded by this may read ``mode.value`` to
    build its message. A plain ``bool`` discards that at the return boundary, and the
    two call sites then need a blanket ``# type: ignore[attr-defined]`` each — which
    would equally swallow a genuine attribute typo in the very guard that pins a
    security property.

    The string must be MODE-SHAPED — drawn wholly from :data:`_OPEN_MODE_CHARS` — and
    not merely contain a forbidden character. That is a second line of defense behind
    :func:`_open_mode_arg`'s choice of argument: a path reaching here is declined on
    its separators and dots rather than accepted on the ``a`` in ``.factory``, which is
    the false READ-ONLY violation a plain contains-check produced on a plain read.
    """
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and bool(mode.value)
        and set(mode.value) <= _OPEN_MODE_CHARS
        and bool(set(mode.value) & _FORBIDDEN_OPEN_MODE_CHARS)
    )


def _opens_with_an_opaque_flag_mask(call: ast.Call) -> bool:
    """Does this ``open`` call pass its flag mask as a bare NUMERIC literal?

    :func:`_forbidden_open_flags` recognises a mask only by the NAMES in it, so
    ``os.open(path, 577)`` — the same bits as ``O_RDONLY | O_CREAT | O_EXCL``, spelled
    without naming any of them — is invisible to it. It is invisible to every other
    branch too: an integer is not a mode string, so :func:`_mode_requests_mutation`
    never sees it either. That leaves one spelling in which a READ-ONLY module can open
    a descriptor for WRITING and pass this guard green, and it is the spelling that
    matters most now that a guarded module holds raw descriptors at all — once the
    descriptor is writable, ``os.write(fd, ...)`` finishes the job under a name too
    generic to forbid receiver-free (see ``_FORBIDDEN_ATTR_CALLS``).

    The mask is treated as a violation rather than DECODED, deliberately. Decoding would
    pin this guard to one platform's flag values, and a read-only module gives up nothing
    by naming ``os.O_RDONLY`` instead of spelling it as a number — so requiring the name
    is free, while guessing at the number is not.

    Both spellings of the mask are checked — positional (``os.open(path, 577)``) and the
    ``flags=`` KEYWORD (``os.open(path, flags=577)``) — for the same reason the call
    names above are matched in both spellings: which one appears is the caller's choice,
    and neither is any less a writable open. The keyword form was the residual left by a
    positional-only check, and it is the easier one to write by accident.

    TWO KNOWN RESIDUALS, stated rather than implied.

    First: a mask that is neither a literal nor a named flag — ``os.open(path, mask)``
    where ``mask`` is a parameter — is undecidable from the syntax alone and is NOT
    caught here. :func:`_forbidden_open_flags` covers the realistic in-module spelling of
    that (a mask hoisted into a local is still built from names it scans at module
    scope); a mask arriving from outside the module is not something a read-only adapter
    has any reason to accept, and catching it would need dataflow this guard deliberately
    does not do.

    Second, and it is the price of not whitelisting receivers: ``os.open("rb", 577)`` —
    a PATH literal spelled wholly out of the READ-only mode characters — is excluded
    here, because it is character-for-character indistinguishable from
    ``path.open("rb", 8192)``, which must stay silent. Only the receiver tells them
    apart, and this guard dropped receiver whitelisting because it was wrong in both
    directions (see :func:`_open_mode_arg`). The exposure is narrow: a mode-shaped path
    that also contains a WRITE character is still caught by
    :func:`_mode_requests_mutation` one branch earlier — ``os.open("w", 577)`` reports as
    ``mode='w'`` — so what escapes is only a file whose name is drawn from ``rbtU``. This
    is the same ambiguity the ``path-literal-that-is-itself-mode-shaped`` case in
    ``tests/unit/test_read_only_guard.py`` already accepts for the mode check, and it is
    accepted here for the same reason: a guard that fired on ``path.open("rb", 8192)``
    would be worked around rather than fixed.

    The bound ``Path.open(mode, buffering)`` form is excluded: its argument 1 is an int
    BY DESIGN, so reading it as a flag mask would fail a plain read. What excludes it is
    argument 0 being MODE-SHAPED — drawn wholly from :data:`_OPEN_MODE_CHARS` — and not
    merely being a string, which is the same shape test :func:`_mode_requests_mutation`
    uses to tell a mode from a path. "Any string constant" is the weaker test and it
    leaks: ``os.open("/target/file.txt", 577)`` has a string at argument 0 too, so
    excluding on stringness alone let a LITERAL path carry a writable mask straight
    through the one check written to stop it.
    """
    for keyword in call.keywords:
        if keyword.arg == "flags" and _is_opaque_int_constant(keyword.value):
            return True
    if len(call.args) < 2 or _is_mode_shaped_constant(call.args[0]):
        return False
    return _is_opaque_int_constant(call.args[1])


def _is_mode_shaped_constant(node: ast.expr) -> bool:
    """Is ``node`` a string literal that could be an open MODE rather than a path?

    A mode is drawn wholly from :data:`_OPEN_MODE_CHARS`; any realistic path literal
    carries a separator, a dot, or a letter outside that alphabet. Shared by the
    numeric-mask check so "this argument is the mode, not the path" is decided ONE way
    — the same rule :func:`_mode_requests_mutation` applies.
    """
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(node.value)
        and set(node.value) <= _OPEN_MODE_CHARS
    )


def _is_opaque_int_constant(node: ast.expr) -> bool:
    """Is ``node`` an integer literal — a flag mask that names no flag?"""
    # ``bool`` is an ``int`` subclass, so it is excluded explicitly rather than left to
    # ``isinstance`` — ``open(f, True)`` is not a flag mask.
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
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
        # Both spellings of every name, because an import decides which one appears and
        # neither is any less a mutation. ``shutil.rmtree(p)`` is an ``ast.Attribute``
        # while ``from shutil import rmtree`` + ``rmtree(p)`` is an ``ast.Name``, and
        # matching only the first meant the whole forbidden set — 26 names, most of them
        # added by the very commit that widened it — was one import statement away from
        # contributing nothing. ``_forbidden_open_flags`` below already recognises both
        # spellings of a flag; these branches make the call names agree with it. Every
        # listed name is receiver-free by construction (see ``_FORBIDDEN_ATTR_CALLS``),
        # which is precisely what makes matching it as a bare name safe.
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if name is None:
            continue
        if name in _FORBIDDEN_ATTR_CALLS:
            violations.append(f"{name}() at line {node.lineno}")
        elif name in _OPEN_ATTR_NAMES:
            # ``path.open(...)`` / ``os.open(...)`` / ``os.fdopen(...)`` and their bare
            # forms. ``file_adapter.runs`` reads through ``os.open`` + ``os.fdopen``
            # rather than ``read_bytes`` so it can bound the read, and an attribute call
            # named ``open`` matches ``_FORBIDDEN_ATTR_CALLS`` not at all — so a later
            # edit to ``path.open("w")`` in a READ-ONLY module used to pass this guard
            # green.
            #
            # WHERE the mode sits depends on the call form (``Path.open(mode)`` is
            # already bound to its path; ``os.fdopen(fd, mode)`` and
            # ``gzip.open(path, mode)`` put it second), and the receiver's name is not a
            # reliable way to tell — see :func:`_open_mode_arg`.
            mode = _open_mode_arg(node, bound=isinstance(func, ast.Attribute))
            if _mode_requests_mutation(mode):
                violations.append(f"{name}(mode={mode.value!r}) at line {node.lineno}")
            elif _opens_with_an_opaque_flag_mask(node):
                # A flag mask spelled as a number names no flag, so the module-scope scan
                # below cannot see it — see :func:`_opens_with_an_opaque_flag_mask`.
                violations.append(f"{name}(flags=<numeric mask>) at line {node.lineno}")

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
