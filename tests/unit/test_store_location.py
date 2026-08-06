"""Unit tests for :mod:`factory_console.store.location`.

Two contracts are pinned here. The first is *where* the db is: the ARCHITECTURE.md
default under the home directory, overridable whole by ``FACTORY_CONSOLE_DB_PATH``
— the seam that keeps the e2e suite, the pytest suite and the developer's own
registry apart — with a set-but-blank override rejected rather than silently
treated as "use the default".

The second is *when* anything is created, and it is the one with teeth:
:func:`resolve_db_path` must create NOTHING, so the local ``factory-console PATH``
viewer can boot and exit without leaving a registry on a machine whose owner never
asked for one. ``test_resolve_db_path_creates_nothing`` is that regression test.

Every case clears ``FACTORY_CONSOLE_DB_PATH`` first, so an ambient value in the
developer's shell can never flip an assertion.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_console.store.location import (
    DEFAULT_DB_FILENAME,
    DEFAULT_STORE_DIRNAME,
    STORE_DIR_MODE,
    ConsoleStoreSettings,
    ensure_store_dir,
    resolve_db_path,
)

DB_PATH_ENV = "FACTORY_CONSOLE_DB_PATH"


@pytest.fixture(autouse=True)
def _no_ambient_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the override so the default cases assert against the default."""
    monkeypatch.delenv(DB_PATH_ENV, raising=False)


def _mode_of(path: Path) -> int:
    """Return just the permission bits of ``path``, without the file-type bits."""
    return stat.S_IMODE(path.stat().st_mode)


def test_default_path_is_the_architecture_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Path.home() rather than HOME, because the default has to hold on a platform
    # where pathlib reads the home directory from somewhere else.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert resolve_db_path() == tmp_path / DEFAULT_STORE_DIRNAME / DEFAULT_DB_FILENAME


def test_env_override_wins_over_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The override names the FILE, not a directory — two parallel runs can point at
    # two files in one tmpdir, which is exactly how the e2e harness uses it.
    override = tmp_path / "run-a" / "console.db"
    monkeypatch.setenv(DB_PATH_ENV, str(override))
    assert resolve_db_path() == override


def test_env_override_expands_a_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A `~`-prefixed override is a normal thing to type into a shell profile; it must
    # not resolve to a literal directory named "~" under the cwd.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv(DB_PATH_ENV, "~/elsewhere/console.db")
    assert resolve_db_path() == tmp_path / "elsewhere" / "console.db"


def test_relative_override_is_made_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    # Callers get an absolute path whatever they were given, so nothing downstream
    # depends on the process's cwd at the moment it opens the db.
    monkeypatch.setenv(DB_PATH_ENV, "console.db")
    assert resolve_db_path().is_absolute()


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"], ids=["empty", "spaces", "whitespace"])
def test_blank_override_is_rejected(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    # The dangerous case: without the validator pydantic coerces "" to Path("."),
    # which resolves to the CWD and would silently put the registry — with v3.1's
    # password hash in it — wherever the console happened to be started from. An
    # empty override is a mistake, not a request for the default.
    monkeypatch.setenv(DB_PATH_ENV, blank)
    with pytest.raises(ValidationError) as excinfo:
        resolve_db_path()
    assert [error["loc"] for error in excinfo.value.errors()] == [("db_path",)]


def test_blank_override_is_rejected_when_passed_directly() -> None:
    with pytest.raises(ValidationError):
        ConsoleStoreSettings(db_path="  ")


def test_unset_override_leaves_db_path_none() -> None:
    # None is resolve_db_path's cue to fall back to the home-directory default.
    assert ConsoleStoreSettings().db_path is None


def test_resolve_db_path_creates_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The regression test for "the viewer must never create a registry": the local
    # `factory-console PATH` viewer boots, serves and exits without ever opening the
    # store, so nothing on that path may leave ~/.factory-console/ behind.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))

    db_path = resolve_db_path()

    assert not db_path.exists()
    assert not db_path.parent.exists()
    assert list(tmp_path.iterdir()) == []


def test_ensure_store_dir_creates_the_tree_at_0700(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "store" / DEFAULT_DB_FILENAME

    parent = ensure_store_dir(db_path)

    assert parent == db_path.parent
    assert parent.is_dir()
    assert _mode_of(parent) == STORE_DIR_MODE
    # The directory only — creating the db file (0600) is schema.py's business.
    assert not db_path.exists()


def test_ensure_store_dir_tightens_a_loose_existing_directory(tmp_path: Path) -> None:
    # The chmod is unconditional precisely for this case: an upgrade from a console
    # that created the directory with the default umask must not leave v3.1's
    # password hash world-readable.
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    loose.chmod(0o755)
    assert _mode_of(loose) == 0o755

    ensure_store_dir(loose / DEFAULT_DB_FILENAME)

    assert _mode_of(loose) == STORE_DIR_MODE


def test_ensure_store_dir_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "store" / DEFAULT_DB_FILENAME

    first = ensure_store_dir(db_path)
    second = ensure_store_dir(db_path)

    assert first == second
    assert _mode_of(second) == STORE_DIR_MODE
