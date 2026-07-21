"""Unit tests for :mod:`factory_console.file_adapter.discovery`.

Pin git-style discovery: ``find_project_root`` walks up (start first) to the
directory holding ``docs/planning/tickets.json`` and returns resolved paths, while
``discover_project`` prefers an explicit path returned verbatim. Comparisons use
``.resolve()`` on the expected root because ``tmp_path`` may sit under a symlinked
prefix (macOS ``/var`` -> ``/private/var``) that the resolved return value follows.
"""

from pathlib import Path

import pytest

from factory_console.file_adapter.discovery import (
    ProjectNotFound,
    discover_project,
    find_project_root,
)


def _write_manifest(root: Path) -> Path:
    """Create ``docs/planning/tickets.json`` under ``root`` and return its path."""
    manifest = root / "docs" / "planning" / "tickets.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("[]", encoding="utf-8")
    return manifest


def test_find_project_root_manifest_at_start(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    assert find_project_root(tmp_path) == tmp_path.resolve()


def test_find_project_root_manifest_levels_up(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == tmp_path.resolve()


def test_find_project_root_not_found_raises(tmp_path: Path) -> None:
    nested = tmp_path / "no" / "manifest" / "here"
    nested.mkdir(parents=True)
    with pytest.raises(ProjectNotFound) as excinfo:
        find_project_root(nested)
    assert excinfo.value.status == 404
    assert excinfo.value.code == "project_not_found"
    assert excinfo.value.starting_dir == nested.resolve()


def test_discover_project_explicit_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFound) as excinfo:
        discover_project(explicit=tmp_path, cwd=tmp_path)
    assert excinfo.value.status == 404
    assert excinfo.value.starting_dir == tmp_path


def test_discover_project_explicit_with_manifest_returns_as_is(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_manifest(project)
    # Returned verbatim (unresolved) — not walked, not normalized.
    assert discover_project(explicit=project, cwd=tmp_path) == project


def test_find_project_root_resolves_symlink(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    _write_manifest(real_root)
    link = tmp_path / "link"
    link.symlink_to(real_root, target_is_directory=True)
    assert find_project_root(link) == real_root.resolve()


def test_discover_project_none_delegates_to_upward_walk(tmp_path: Path) -> None:
    _write_manifest(tmp_path)
    nested = tmp_path / "x" / "y"
    nested.mkdir(parents=True)
    assert discover_project(explicit=None, cwd=nested) == tmp_path.resolve()
