"""Where the console's own SQLite store lives, and how that answer is reached.

The default is ``~/.factory-console/console.db``, named as the contract by
ARCHITECTURE.md's v3 "Console-owned store" section — this module is only its
implementation, not a second authority on the path.
``FACTORY_CONSOLE_DB_PATH`` overrides it whole, which is what keeps the Playwright
e2e suite, the pytest suite and the developer's own console out of each other's
registry. There is deliberately **no** ``XDG_DATA_HOME`` lookup: adding it would
give "where is my db" a second answer for the sake of a convention this project
already settled, and one env var overriding the entire path is both simpler and
strictly more capable.

Resolution is split from creation on purpose. :func:`resolve_db_path` is pure;
:func:`ensure_store_dir` is the only thing here that touches the filesystem. That
split is what lets the local ``factory-console PATH`` viewer boot, serve and exit
without ever creating ``~/.factory-console/`` on a machine whose owner never asked
for a registry.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from factory_console.file_adapter.path_safety import resolve_or_none

# The store directory and db file names behind the ARCHITECTURE.md default,
# ``~/.factory-console/console.db``. Named rather than inlined so the tests assert
# against the same two strings the resolver builds the path from.
DEFAULT_STORE_DIRNAME = ".factory-console"
DEFAULT_DB_FILENAME = "console.db"

# Permissions for the store directory. 0700 is not decoration: v3.1 puts a password
# hash in this store, and establishing the mode at creation means v3.1 inherits a
# correctly-permissioned tree rather than shipping a chmod against directories
# already in the wild.
STORE_DIR_MODE = 0o700

# The one wording for a set-but-empty override, so the operator sees the same
# sentence however the settings object was built.
_BLANK_DB_PATH_ERROR = (
    "FACTORY_CONSOLE_DB_PATH must name a database file, "
    f"or be unset to use ~/{DEFAULT_STORE_DIRNAME}/{DEFAULT_DB_FILENAME}"
)


class ConsoleStoreSettings(BaseSettings):
    """Just the console store's location, readable without touching the rest of the config.

    Narrow on purpose, in the same spirit as
    :class:`~factory_console.config.WriteTokenSettings`: a caller that only wants the
    db path must not re-validate ``host``/``port``/``log_level`` as a side effect, or
    an unrelated non-loopback ``FACTORY_CONSOLE_HOST`` in the environment would abort
    a caller that never consulted it.
    """

    model_config = SettingsConfigDict(env_prefix="FACTORY_CONSOLE_")

    # An operator/test override naming the db FILE (not a directory), so two parallel
    # test runs can point at two files in one tmpdir. ``None`` — the default, and the
    # normal case — means the ARCHITECTURE.md default under the home directory.
    db_path: Path | None = None

    @field_validator("db_path", mode="before")
    @classmethod
    def _reject_blank_db_path(cls, value: object) -> object:
        """Reject a set-but-empty override instead of resolving it to the cwd.

        Runs in ``mode="before"`` because an env var arrives as a ``str``: by the time
        pydantic has coerced it, ``""`` is already ``Path('.')``-shaped nonsense rather
        than something a validator can tell apart from a deliberate path. An empty
        ``FACTORY_CONSOLE_DB_PATH=`` is a mistake — a variable someone meant to set and
        didn't — not a request for the default, so it fails fast here.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError(_BLANK_DB_PATH_ERROR)
            return stripped
        return value


def resolve_db_path() -> Path:
    """Return the absolute path of the console's SQLite store.

    **Pure**: it stats nothing and creates nothing, so it is safe to call at import
    or boot time, and safe in a viewer session that will never open the db.
    Creating the directory is :func:`ensure_store_dir`'s job, and nothing else's.

    ``FACTORY_CONSOLE_DB_PATH`` wins when set, and it names the **file**, not a
    directory — so two parallel test runs can point at two files in one tmpdir.
    Otherwise the path is ARCHITECTURE.md's default,
    ``~/.factory-console/console.db``. Either way the result is expanded and
    resolved non-strictly, because the file legitimately does not exist yet.

    Raises:
        ValueError: ``FACTORY_CONSOLE_DB_PATH`` is set but blank, or the path
            could not be resolved (a symlink loop, or an unencodable path) —
            raised as a plain ``ValueError`` rather than pydantic's
            ``ValidationError``, matching :func:`~factory_console.config.read_write_token`,
            so a boot path can catch one exception type for every bad override.
    """
    try:
        db_path = ConsoleStoreSettings().db_path
    except ValidationError as exc:
        raise ValueError(_BLANK_DB_PATH_ERROR) from exc
    if db_path is None:
        db_path = Path.home() / DEFAULT_STORE_DIRNAME / DEFAULT_DB_FILENAME
    # One expansion rule for both branches, so an override and the default cannot
    # drift into being normalized differently. resolve_or_none (not a raw
    # .resolve(strict=False)) because that call is not total — see its docstring.
    resolved = resolve_or_none(db_path.expanduser())
    if resolved is None:
        raise ValueError(f"could not resolve FACTORY_CONSOLE_DB_PATH {db_path}")
    return resolved


def ensure_store_dir(db_path: Path) -> Path:
    """Create ``db_path``'s parent directory at mode 0700 and return it.

    The **only** directory-creating function in the store. It is called from the real
    registry's first-touch path — not from its constructor — so that constructing a
    registry, or booting the viewer, still creates nothing.

    Refuses a ``db_path`` whose parent IS the home directory or the system temp
    root: ``FACTORY_CONSOLE_DB_PATH`` names a file, not a directory, so an override
    of ``~/console.db`` or ``/tmp/console.db`` would otherwise chmod a directory
    this store does not own and everything else on the machine shares. Both sides
    of that check are resolved the same way ``resolve_db_path`` resolves its own
    output, and the temp root is checked as both ``tempfile.gettempdir()`` (the
    per-process ``TMPDIR``) and the literal ``/tmp`` — on macOS the two differ, so
    an override of ``/tmp/console.db`` would otherwise resolve to ``/private/tmp``
    and slip past a check that only knew about ``TMPDIR``.

    Any directory this call creates — the parent and, for a multi-level override,
    the ancestors ``mkdir(parents=True, ...)`` had to create along with it — is
    chmod'd to 0700 explicitly: ``mkdir``'s ``mode`` argument is applied only to
    the deepest directory it creates, not to the intermediate ones made along the
    way, so those would otherwise sit at the umask default. The chmod also runs
    unconditionally on a parent that already existed, so a loose pre-existing
    ``~/.factory-console/`` is tightened rather than left as found. The db FILE's
    0600 mode is not this function's business: ``schema.py`` sets it at the point
    it creates the file.
    """
    parent = db_path.parent
    resolved_parent = resolve_or_none(parent)
    if resolved_parent is None:
        raise ValueError(f"could not resolve store directory {parent}")
    shared_dirs = {
        resolve_or_none(Path.home()),
        resolve_or_none(Path(tempfile.gettempdir())),
        resolve_or_none(Path("/tmp")),
    }
    if resolved_parent in shared_dirs:
        raise ValueError(
            "FACTORY_CONSOLE_DB_PATH must name a file inside its own directory, "
            f"not directly under the shared directory {resolved_parent}"
        )
    to_tighten = []
    node = resolved_parent
    while not node.exists():
        to_tighten.append(node)
        node = node.parent
    resolved_parent.mkdir(parents=True, exist_ok=True, mode=STORE_DIR_MODE)
    if resolved_parent not in to_tighten:
        to_tighten.append(resolved_parent)
    for directory in to_tighten:
        os.chmod(directory, STORE_DIR_MODE)
    return resolved_parent
