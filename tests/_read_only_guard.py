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
        "renames",
        "makedirs",
        "removedirs",
        # shutil's mutating surface. The module docstring cites ``shutil.move`` as the
        # example of what belongs here, so its absence read as coverage that was never
        # there — ``shutil.rmtree`` in a READ-ONLY module used to pass this guard green.
        # ``move`` itself is collision-prone and lives in
        # :data:`_RECEIVER_QUALIFIED_ATTR_CALLS`.
        "rmtree",
        "copyfile",
        "copyfileobj",
        "copy2",
        "copytree",
        # os-level creation and metadata mutation. None of these writes bytes, but each
        # creates or alters something under the observed project, which the READ-ONLY
        # header forbids just as squarely.
        "symlink",
        "mknod",
        "mkfifo",
        "chmod",
        "chown",
        "utime",
        # The PATHLIB spellings of the two names above it. This set pairs the os and
        # pathlib spellings wherever NEITHER collides — ``mkdir``/``makedirs``,
        # ``rmdir``/``removedirs`` — and the pairing is the reason it cannot simply be
        # "every mutating name": ``unlink``/``remove``, ``rename``/``replace`` and
        # ``link``/``hardlink_to`` each have a half that names an ordinary method too, and
        # those halves live in :data:`_RECEIVER_QUALIFIED_ATTR_CALLS` instead. Which half
        # collides is not predictable from which library it comes from — ``unlink`` is
        # pathlib's and is collision-free, ``rename`` is pathlib's and is NOT
        # (``DataFrame.rename``) — so membership is decided per name, never per library.
        #
        # The two below are here because ``symlink``/``link`` arrived without their
        # ``Path`` counterparts, and this codebase is pathlib-first — every guarded module
        # threads ``Path`` objects and the guard's own positive tests are written as
        # ``path.mkdir()`` — so the spelling a read-only module would actually reach for
        # was the one spelling not covered.
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
# Mutating names that DO collide with ordinary non-filesystem methods, so the set above
# cannot hold them: matched receiver-free there, ``status.replace("_", "-")`` and
# ``candidates.remove(x)`` both reported as filesystem mutations, and a guard that fires
# on a plain read is the failure this module's own tests call the one that gets it worked
# around rather than fixed. They were listed there regardless, so the "every listed name
# is receiver-free by construction" claim the call walk used to make was false of every
# name now in this set. The count is deliberately not quoted, for the reason stated where
# the call walk refuses to quote the other set's size: it would go stale on the next name
# either set gains.
#
# They are still matched in the BARE spelling (``remove(path)`` after ``from os import
# remove``), where the collision does not arise: ``list.remove``/``str.replace`` are
# METHODS and always carry a receiver, so a bare call to one of these names in a
# read-only module is the filesystem function. Only the ATTRIBUTE spelling needs the
# receiver, and :func:`_is_ambiguous_name_mutating` decides it.
_RECEIVER_QUALIFIED_ATTR_CALLS = frozenset(
    {"replace", "rename", "remove", "move", "link", "truncate"}
)
# The subset of the names above that pathlib ALSO spells, where the filesystem call takes
# exactly one argument and the colliding method does not — see
# :func:`_is_ambiguous_name_mutating`, which uses arity to keep ``path.rename(other)``
# caught without catching ``frame.rename(columns=...)``.
_PATHLIB_SINGLE_ARG_MUTATIONS = frozenset({"replace", "rename"})
# Receivers on which a name from the set above is unambiguously the filesystem call.
_FS_MODULE_RECEIVERS = frozenset({"os", "shutil"})
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

    That shape test is DELEGATED to :func:`_is_mode_shaped_constant` rather than
    restated here. The two were spelled out independently — four identical clauses,
    then one extra — in a module whose whole reason for existing is that "a copied rule
    ... drifts back apart": a later change to what counts as mode-shaped would have had
    to land in both or this guard and the numeric-mask check would silently stop
    agreeing about which argument is the mode.
    """
    # Narrowed in the POSITIVE branch, not by an early `if not ...: return False`.
    # :class:`TypeGuard` (unlike PEP 742's ``TypeIs``) narrows only where the guard
    # answered True, so the negative-early-return spelling leaves ``mode`` as
    # ``ast.expr | None`` on the line that reads ``.value`` — an unchecked attribute
    # access on a base class that does not define it.
    if _is_mode_shaped_constant(mode):
        return bool(set(mode.value) & _FORBIDDEN_OPEN_MODE_CHARS)
    return False


def _opens_with_an_opaque_flag_mask(call: ast.Call, mask_names: set[str]) -> bool:
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
    where ``mask`` is a PARAMETER — is undecidable from the syntax alone and is NOT caught
    here. Two module-scope scans cover the in-module spellings between them:
    :func:`_forbidden_open_flags` for a mask built from flag NAMES, and
    :func:`_opaque_flag_mask_names` for one built from a bare NUMBER. The second was
    added because the first does not in fact cover it — this paragraph used to claim "a
    mask hoisted into a local is still built from names it scans at module scope", which
    holds only while the local is built from names, and ``flags = 0o1101`` builds one from
    none. A mask arriving from outside the module is not something a read-only adapter has
    any reason to accept, and catching it would need dataflow this guard deliberately does
    not do.

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
        if keyword.arg == "flags" and _is_opaque_mask(keyword.value, mask_names):
            return True
    if len(call.args) < 2 or _is_mode_shaped_constant(call.args[0]):
        return False
    return _is_opaque_mask(call.args[1], mask_names)


def _is_opaque_mask(node: ast.expr, mask_names: set[str]) -> bool:
    """Is this flag argument an opaque mask, whether spelled inline or via a local?

    Two spellings, one answer. Inline is the literal (or a ``|`` carrying one), and
    :func:`_is_opaque_flag_expression` decides it. Via a local is an ``ast.Name`` that
    :func:`_opaque_flag_mask_names` already resolved at module scope — the spelling that
    used to escape, because reading the call alone showed only the name.
    """
    if _is_opaque_flag_expression(node):
        return True
    return isinstance(node, ast.Name) and node.id in mask_names


def _is_mode_shaped_constant(node: ast.expr | None) -> TypeGuard[ast.Constant]:
    """Is ``node`` a string literal that could be an open MODE rather than a path?

    A mode is drawn wholly from :data:`_OPEN_MODE_CHARS`; any realistic path literal
    carries a separator, a dot, or a letter outside that alphabet. The SINGLE owner of
    "this argument is the mode, not the path", so the numeric-mask check
    (:func:`_opens_with_an_opaque_flag_mask`) and the mode check
    (:func:`_mode_requests_mutation`) decide it ONE way and cannot drift apart.

    ``None`` is accepted (and answered ``False``) so :func:`_mode_requests_mutation` can
    hand its optional argument straight through; :class:`TypeGuard` so the narrowing
    survives the call and a guarded caller may read ``.value`` without a blanket
    ``# type: ignore``.
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


def _is_ambiguous_name_mutating(func: ast.Attribute, call: ast.Call, receivers: set[str]) -> bool:
    """Is this ATTRIBUTE call of a :data:`_RECEIVER_QUALIFIED_ATTR_CALLS` name a mutation?

    Only the attribute spelling reaches here, and only for a name that collides with an
    ordinary method, so the receiver is what tells ``os.remove(p)`` from
    ``candidates.remove(x)``. Two ways to answer yes:

    1. the receiver names a filesystem MODULE — ``os.replace``, ``shutil.move``,
       ``os.truncate`` — whatever the arity. ``receivers`` is
       :func:`_fs_module_receiver_names`'s answer and NOT the bare
       :data:`_FS_MODULE_RECEIVERS` constant, because a module may be imported under
       another name: ``import shutil as sh`` makes ``sh.move(a, b)`` the same call, and
       matching the literal spellings alone let it through — which the receiver-free
       matching this function replaced had caught;
    2. the call is a :data:`_PATHLIB_SINGLE_ARG_MUTATIONS` name with exactly ONE
       positional argument and no keywords — ``path.replace(target)`` /
       ``path.rename(target)``, both renames. Their colliding twins do not have that
       shape: ``str.replace`` takes at least two arguments and ``DataFrame.rename`` is
       called with keywords, so arity separates them where the receiver's name cannot.
       This codebase is pathlib-first, so those spellings are the ones a read-only module
       would actually reach for, and dropping them to fix the false positive would have
       traded one silent gap for another.

    TWO accepted residuals, stated rather than implied.

    First: a ``remove``/``move``/``link``/``truncate`` called on a NON-module receiver
    bound to a path — ``p.truncate(0)`` on an open file object — is not caught, because
    nothing in the syntax separates it from ``candidates.remove(x)`` and this guard does no
    type inference. ``unlink``, the pathlib spelling a module would use to delete, is
    collision-free and stays matched receiver-free in :data:`_FORBIDDEN_ATTR_CALLS`.

    Second, and it is the price of deciding rule 2 on arity: the KEYWORD spelling of those
    same renames — ``path.replace(target=other)`` — has no positional argument, so it does
    not match and is not caught. Every other spelling-pair in this guard is checked in both
    forms deliberately, and this one is the exception, because the keyword form is also
    exactly what tells ``DataFrame.rename(columns=...)`` apart. Closing it needs the type
    of the receiver, which is the inference this guard does not do; the ``os.``-qualified
    spelling of both names remains caught either way.
    """
    if _receiver_name(func) in receivers:
        return True
    return func.attr in _PATHLIB_SINGLE_ARG_MUTATIONS and len(call.args) == 1 and not call.keywords


def _fs_module_receiver_names(tree: ast.AST) -> set[str]:
    """Every local name bound to a filesystem module, including aliases.

    Starts from the literal spellings (:data:`_FS_MODULE_RECEIVERS`) and adds whatever
    ``import os as _os`` / ``import shutil as sh`` bound them to. Without this, qualifying
    the collision-prone names by receiver would be a DETECTION LOSS rather than a
    false-positive fix: ``sh.move(a, b)`` is exactly the ``shutil.move`` the forbidden set
    exists to catch, and the guard's own tests already exercise an aliased ``os`` in the
    ``_os.fdopen(fd, "wb")`` case, so the spelling is one this codebase expects to see.

    The literals are kept as a floor because a receiver need not come from an import at
    all — this guard's own tests hand ``os``/``shutil`` in as function PARAMETERS.
    """
    names = set(_FS_MODULE_RECEIVERS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name in _FS_MODULE_RECEIVERS
            )
    return names


def _receiver_name(func: ast.Attribute) -> str | None:
    """The NAME of an attribute call's receiver: ``os`` for ``os.remove``, else ``None``.

    A dotted receiver answers its LAST component (``os.path.foo`` → ``path``), the same
    shape :func:`assert_module_is_read_only` uses to derive a call's own name.

    This resolves SPELLING only, never binding: an ``import os as _os`` receiver answers
    ``_os``, because that is what the source says. Mapping that back to the module it
    names is :func:`_fs_module_receiver_names`'s job, and the two are kept apart so the
    alias handling lives in one place rather than being half-done here.
    """
    value = func.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _is_opaque_flag_expression(node: ast.expr) -> bool:
    """Is ``node`` a flag mask that names no flag — a bare int, or a ``|`` with one in it?

    Recurses ONLY through bitwise-OR operands, never through arbitrary subexpressions.
    That bound is load-bearing rather than incidental: ``file_adapter.runs`` builds its
    mask as ``os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | ...``, whose ``0`` defaults are
    int constants sitting inside CALL arguments. A blanket walk would find them and report
    that module's portability idiom — the one the ``getattr`` branch of
    :func:`_forbidden_open_flags` exists to bless — as an opaque mask.
    """
    if _is_opaque_int_constant(node):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_opaque_flag_expression(node.left) or _is_opaque_flag_expression(node.right)
    return False


def _opaque_flag_mask_names(tree: ast.AST) -> set[str]:
    """Names bound to a mask that NAMES no flag, collected over the whole module.

    The numeric twin of :func:`_forbidden_open_flags`, and it exists for the same reason
    that one is module-scoped. :func:`_opens_with_an_opaque_flag_mask` reads only what the
    CALL passes, so ``flags = 0o1101`` on one line and ``os.open(path, flags)`` on the next
    showed it an ``ast.Name`` and nothing else — while the flag-NAME scan found no flag
    named, because a bare octal names none. That is one spelling in which a read-only
    module opens a writable descriptor and passes both checks green, and it is one line
    away from the shape ``file_adapter.runs`` already uses.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_opaque_flag_expression(node.value):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif (
            isinstance(node, ast.AnnAssign)
            and node.value is not None
            and _is_opaque_flag_expression(node.value)
            and isinstance(node.target, ast.Name)
        ):
            names.add(node.target.id)
    return names


def _forbidden_imported_names(tree: ast.AST) -> list[str]:
    """Every mutating name IMPORTED anywhere under ``tree``, whatever it was renamed to.

    The call walk matches a name as written, so ``from shutil import rmtree as _rm`` +
    ``_rm(p)`` presents an ``ast.Name`` reading ``_rm`` and matches nothing — the same
    bypass the bare-name branch was added to close, one ``as`` clause further along. The
    aliased flag spelling (``from os import O_CREAT as _C``) defeats
    :func:`_forbidden_open_flags` identically.

    Matched on the IMPORTED name rather than the local alias, so the rename is irrelevant,
    and the import ITSELF is the violation rather than any call through it — the rule
    :func:`_forbidden_open_flags` already states for flags ("a read-only module has no
    reason to NAME a write flag anywhere") applied to the mutating functions too. That
    also covers an alias called through a form no name-matcher sees, such as a dict of
    handlers.
    """
    forbidden = _FORBIDDEN_ATTR_CALLS | _RECEIVER_QUALIFIED_ATTR_CALLS | _FORBIDDEN_OPEN_FLAGS
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.update(alias.name for alias in node.names if alias.name in forbidden)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name.rsplit(".", 1)[-1]
                for alias in node.names
                if alias.name.rsplit(".", 1)[-1] in forbidden
            )
    return sorted(found)


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
    # Both module-scope scans run BEFORE the call walk, because each answers a question a
    # single call node cannot — see :func:`_opaque_flag_mask_names`.
    mask_names = _opaque_flag_mask_names(tree)
    fs_receivers = _fs_module_receiver_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Both spellings of every name, because an import decides which one appears and
        # neither is any less a mutation. ``shutil.rmtree(p)`` is an ``ast.Attribute``
        # while ``from shutil import rmtree`` + ``rmtree(p)`` is an ``ast.Name``, and
        # matching only the first meant the whole forbidden set — most of it added by the
        # very commit that widened it — was one import statement away from contributing
        # nothing. The set's SIZE is deliberately not quoted here: an earlier revision
        # said "26 names" and was already wrong by seven the day it was written, which is
        # the same stale-count drift the module docstring refuses to reintroduce for the
        # adopter list. ``_forbidden_open_flags`` below already recognises both
        # spellings of a flag; these branches make the call names agree with it.
        #
        # ``_FORBIDDEN_ATTR_CALLS`` is the receiver-free half, and it is now ONLY that:
        # every name that collides with an ordinary method moved to
        # ``_RECEIVER_QUALIFIED_ATTR_CALLS``, whose attribute spelling is decided by
        # :func:`_is_ambiguous_name_mutating` instead. (No count here either, for the
        # reason given just above — the split has already gained a name since it was made.)
        # This comment used to assert every listed name was "receiver-free by
        # construction", which was false of the moved ones and made the guard fire on
        # ``status.replace("_", "-")``. Neither set is matched through an ALIAS here — an
        # import cannot be seen from a call node — so :func:`_forbidden_imported_names`
        # covers that spelling at module scope.
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
        elif name in _RECEIVER_QUALIFIED_ATTR_CALLS:
            # Bare spelling is unambiguous (a colliding method always has a receiver);
            # the attribute spelling needs one — see :func:`_is_ambiguous_name_mutating`.
            if isinstance(func, ast.Name) or _is_ambiguous_name_mutating(func, node, fs_receivers):
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
            elif _opens_with_an_opaque_flag_mask(node, mask_names):
                # A flag mask spelled as a number names no flag, so the module-scope scan
                # below cannot see it — see :func:`_opens_with_an_opaque_flag_mask`.
                violations.append(f"{name}(flags=<numeric mask>) at line {node.lineno}")

    # Flags are collected over the WHOLE module rather than per call — see
    # :func:`_forbidden_open_flags` for why a per-call walk could not see the mask
    # ``file_adapter.runs`` actually builds.
    violations.extend(
        f"names the mutating open flag {flag}" for flag in _forbidden_open_flags(tree)
    )
    # Imports are collected over the whole module for the same reason, and they catch the
    # ALIASED spelling of everything above — see :func:`_forbidden_imported_names`.
    violations.extend(
        f"imports the mutating name {imported}" for imported in _forbidden_imported_names(tree)
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
