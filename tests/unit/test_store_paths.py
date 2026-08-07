"""Unit tests for :mod:`factory_console.store.paths`.

One contract is pinned here, and it is the identity rule the whole registry rests
on: two spellings of one directory must canonicalise to the SAME
:class:`~pathlib.Path`, because that is what makes the ``UNIQUE`` index on
``projects.path`` mean "one project" instead of "one way of typing a project".
``~``, a symlinked alias, a trailing slash and a ``..`` detour are each one of
those spellings, and each has a case below.

Two cases are regressions rather than examples. ``strict=False`` is load-bearing
— a path that no longer exists must still canonicalise, so the store can hold a
row for an unplugged drive — and a RELATIVE path must be REFUSED rather than
resolved against a server working directory the caller cannot see. Tightening
either one would break the registry in a way no other test would notice.

Every case that involves ``~`` points ``HOME`` at ``tmp_path`` first, so a
developer's real home directory can never flip an assertion, and expectations are
resolved too, since a ``tmp_path`` under a symlinked ``/tmp`` or ``/var`` is the
normal case on macOS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from factory_console.errors import to_error_response
from factory_console.store.paths import (
    InvalidProjectPath,
    canonical_project_path,
    default_project_name,
)


def test_tilde_expands_to_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert canonical_project_path("~/dev/foo") == (tmp_path / "dev" / "foo").resolve()


def test_symlink_and_real_path_canonicalise_equal(tmp_path: Path) -> None:
    real = tmp_path / "real-project"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    # The reason the UNIQUE index can be trusted: one directory, two names, one row.
    assert canonical_project_path(alias) == canonical_project_path(real)


def test_tilde_and_absolute_spelling_canonicalise_equal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "dev" / "foo"
    project.mkdir(parents=True)
    assert canonical_project_path("~/dev/foo") == canonical_project_path(project)


def test_relative_path_is_refused_not_resolved() -> None:
    # Resolving it would silently address the server's cwd, which the caller
    # cannot see — so this must stay a refusal, never a resolution.
    with pytest.raises(InvalidProjectPath) as excinfo:
        canonical_project_path("relative/project")
    assert excinfo.value.details == {"path": "relative/project"}


def test_dot_is_refused_as_a_relative_path() -> None:
    with pytest.raises(InvalidProjectPath):
        canonical_project_path(".")


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_path_is_refused(blank: str) -> None:
    # Path("") is Path("."), so blankness has to be caught on the string.
    with pytest.raises(InvalidProjectPath):
        canonical_project_path(blank)


def test_nonexistent_path_canonicalises_fine(tmp_path: Path) -> None:
    # The strict=False regression: a deleted directory or an unmounted volume
    # must still produce a canonical path, so the row survives to be read back
    # and reported as a named condition.
    missing = tmp_path / "gone" / "project"
    assert not missing.exists()
    assert canonical_project_path(missing) == missing.resolve()


def test_trailing_slash_and_parent_hops_normalise(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    canonical = canonical_project_path(project)
    assert canonical_project_path(f"{project}/") == canonical
    assert canonical_project_path(tmp_path / "project" / ".." / "project") == canonical


def test_expanduser_failure_becomes_invalid_project_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ~nosuchuser raises RuntimeError on some platforms; it must surface as the
    # 400 it is, not as an unmapped 500.
    def _boom(self: Path) -> Path:
        raise RuntimeError("no home directory")

    monkeypatch.setattr(Path, "expanduser", _boom)
    with pytest.raises(InvalidProjectPath):
        canonical_project_path("~nosuchuser/project")


def test_resolve_failure_becomes_invalid_project_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # resolve(strict=False) is not non-raising: a symlink loop is a RuntimeError
    # through CPython 3.12. Same rule — a 400, never a 500.
    def _boom(self: Path, strict: bool = False) -> Path:
        raise RuntimeError("Symlink loop from '/looped'")

    monkeypatch.setattr(Path, "resolve", _boom)
    with pytest.raises(InvalidProjectPath):
        canonical_project_path("/looped")


def test_invalid_project_path_carries_the_transport_contract() -> None:
    error = InvalidProjectPath("relative/project", reason="Project path must be absolute")
    assert error.code == "invalid_project_path"
    assert error.status == 400
    assert to_error_response(error) == {
        "error": {
            "code": "invalid_project_path",
            "message": "Project path must be absolute",
            "details": {"path": "relative/project"},
        }
    }


def test_default_project_name_is_the_final_component() -> None:
    assert default_project_name(Path("/Users/me/dev/factory-console")) == "factory-console"


def test_default_project_name_falls_back_for_a_root() -> None:
    # Path("/").name is "", and RegisteredProject.name is min_length=1, so the
    # fallback is what keeps a root-registered row constructible at all.
    assert default_project_name(Path("/")) == "/"
