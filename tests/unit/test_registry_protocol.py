"""Unit tests for :mod:`factory_console.store.registry_protocol`.

The port itself has no behaviour to test — it is a Protocol — so what is pinned
here is the part of it that IS executable: the two errors' transport contract,
and the structural check every future implementation and fake will be asserted
with.

The error cases assert the rendered envelope, not just the attributes, because
the code, the status and the ``details`` keys (``existingId``, ``projectId`` —
camelCase, like the rest of the REST v1 surface) are what a client is written
against; a rename that kept the attributes intact would still break the SPA.

The structural case pins ``@runtime_checkable`` and the SEVEN-method surface,
including the negative: a class missing one method must NOT satisfy
``isinstance``. Without that, "the stub conforms" would be true of any object at
all and the check would pin nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from factory_console.domain.registry import RegisteredProject
from factory_console.errors import to_error_response
from factory_console.store.registry_protocol import (
    DuplicateProjectPath,
    ProjectNotRegistered,
    ProjectRegistry,
)

EXISTING_ID = "0" * 32


class StubRegistry:
    """A do-nothing class carrying exactly the seven port methods.

    Deliberately does NOT inherit from :class:`ProjectRegistry` — the point is
    that conformance is STRUCTURAL, the way the SQLite implementation and the
    in-memory fake will each satisfy the port without a shared base class.
    """

    def add_project(self, path: Path | str, name: str | None = None) -> RegisteredProject:
        return RegisteredProject(
            id=EXISTING_ID,
            name=name or "stub",
            path=Path("/stub"),
            addedAt=datetime.now(UTC),
        )

    def list_projects(self) -> list[RegisteredProject]:
        return []

    def get_project(self, project_id: str) -> RegisteredProject | None:
        return None

    def find_by_path(self, path: Path | str) -> RegisteredProject | None:
        return None

    def remove_project(self, project_id: str) -> bool:
        return False

    def get_selected_project(self) -> RegisteredProject | None:
        return None

    def set_selected_project(self, project_id: str | None) -> RegisteredProject | None:
        return None


class PartialRegistry:
    """Six of the seven methods — :meth:`set_selected_project` is missing."""

    def add_project(self, path: Path | str, name: str | None = None) -> RegisteredProject:
        raise NotImplementedError

    def list_projects(self) -> list[RegisteredProject]:
        return []

    def get_project(self, project_id: str) -> RegisteredProject | None:
        return None

    def find_by_path(self, path: Path | str) -> RegisteredProject | None:
        return None

    def remove_project(self, project_id: str) -> bool:
        return False

    def get_selected_project(self) -> RegisteredProject | None:
        return None


def test_duplicate_project_path_carries_the_transport_contract() -> None:
    error = DuplicateProjectPath(Path("/Users/me/dev/foo"), EXISTING_ID)
    assert error.code == "duplicate_project_path"
    assert error.status == 409
    assert error.details == {"path": "/Users/me/dev/foo", "existingId": EXISTING_ID}


def test_duplicate_project_path_renders_through_to_error_response() -> None:
    error = DuplicateProjectPath("/Users/me/dev/foo", EXISTING_ID)
    assert to_error_response(error) == {
        "error": {
            "code": "duplicate_project_path",
            "message": "A project is already registered at /Users/me/dev/foo",
            "details": {"path": "/Users/me/dev/foo", "existingId": EXISTING_ID},
        }
    }


def test_project_not_registered_carries_the_transport_contract() -> None:
    error = ProjectNotRegistered(EXISTING_ID)
    assert error.code == "project_not_registered"
    assert error.status == 404
    assert error.details == {"projectId": EXISTING_ID}


def test_project_not_registered_renders_through_to_error_response() -> None:
    assert to_error_response(ProjectNotRegistered(EXISTING_ID)) == {
        "error": {
            "code": "project_not_registered",
            "message": f"No registered project with id {EXISTING_ID}",
            "details": {"projectId": EXISTING_ID},
        }
    }


def test_port_is_runtime_checkable_and_a_seven_method_stub_conforms() -> None:
    assert isinstance(StubRegistry(), ProjectRegistry)


def test_a_class_missing_a_port_method_does_not_conform() -> None:
    assert not isinstance(PartialRegistry(), ProjectRegistry)
