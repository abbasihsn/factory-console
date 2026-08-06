"""Unit tests for the registry domain vocabulary.

These pin the contract every v3.0 track builds on: registry-id validation
(:data:`REGISTERED_PROJECT_ID_PATTERN`), ``frozen`` / ``extra='forbid'`` config,
``model_dump`` round-trips, and the EXHAUSTIVE membership of
:data:`RegistryEntryCondition` — the union the SPA's generated types derive from,
so a sixth condition added without telling the frontend fails here. Deterministic
and I/O-free — pydantic + stdlib only.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from factory_console.domain import (
    REGISTERED_PROJECT_ID_PATTERN,
    RegisteredProject,
    RegistryEntry,
    RegistryEntryCondition,
)

VALID_ID = "0f9c2b4a6d8e4f1a9b3c5d7e8f0a1b2c"


def _make_registered_project(project_id: str = VALID_ID) -> RegisteredProject:
    return RegisteredProject(
        id=project_id,
        name="Factory Console",
        path=Path("/Users/dev/projects/factory-console"),
        addedAt=datetime(2026, 8, 6, 12, 30, tzinfo=UTC),
    )


def _make_entry(condition: RegistryEntryCondition = "ok") -> RegistryEntry:
    return RegistryEntry(project=_make_registered_project(), condition=condition)


# --------------------------------------------------------------------------- #
# Registry-id validation
# --------------------------------------------------------------------------- #


def test_registered_project_id_pattern_is_the_documented_constant() -> None:
    # The API boundary and the store import this verbatim, so it is pinned.
    assert REGISTERED_PROJECT_ID_PATTERN == r"^[0-9a-f]{32}$", (
        "REGISTERED_PROJECT_ID_PATTERN must not drift — it is the single source "
        "of truth imported verbatim by the API boundary and the store"
    )


def test_valid_uuid4_hex_id_is_accepted() -> None:
    assert _make_registered_project().id == VALID_ID


@pytest.mark.parametrize(
    "invalid_id",
    [
        pytest.param("", id="empty"),
        pytest.param("0f9c2b4a6d8e4f1a9b3c5d7e8f0a1b2", id="31-chars-too-short"),
        pytest.param("0f9c2b4a6d8e4f1a9b3c5d7e8f0a1b2cd", id="33-chars-too-long"),
        pytest.param("0F9C2B4A6D8E4F1A9B3C5D7E8F0A1B2C", id="uppercase-hex"),
        pytest.param("0f9c2b4a-6d8e-4f1a-9b3c-5d7e8f0a1b2c", id="dashed-uuid-form"),
        pytest.param("0f9c2b4a6d8e4f1a9b3c5d7e8f0a1b2g", id="non-hex-character"),
        pytest.param(" 0f9c2b4a6d8e4f1a9b3c5d7e8f0a1b2c", id="leading-space"),
        pytest.param("../0f9c2b4a6d8e4f1a9b3c5d7e8f0a1b", id="traversal-attempt"),
    ],
)
def test_invalid_registry_id_is_rejected(invalid_id: str) -> None:
    with pytest.raises(ValidationError):
        _make_registered_project(invalid_id)


def test_blank_name_is_rejected() -> None:
    # An unnamed row is unaddressable in a project switcher.
    with pytest.raises(ValidationError):
        RegisteredProject(
            id=VALID_ID,
            name="",
            path=Path("/proj"),
            addedAt=datetime(2026, 8, 6, 12, 30, tzinfo=UTC),
        )


def test_unknown_condition_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RegistryEntry(project=_make_registered_project(), condition="availability")


def test_models_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        RegisteredProject(
            id=VALID_ID,
            name="Factory Console",
            path=Path("/proj"),
            addedAt=datetime(2026, 8, 6, 12, 30, tzinfo=UTC),
            availability="ok",
        )


# --------------------------------------------------------------------------- #
# Immutability
# --------------------------------------------------------------------------- #


def test_registered_project_is_frozen() -> None:
    project = _make_registered_project()
    with pytest.raises(ValidationError):
        project.name = "Renamed"


def test_registry_entry_is_frozen() -> None:
    entry = _make_entry()
    with pytest.raises(ValidationError):
        entry.condition = "unreadable"


# --------------------------------------------------------------------------- #
# Serialization round-trips
# --------------------------------------------------------------------------- #


def test_registered_project_round_trips_through_model_dump() -> None:
    project = _make_registered_project()
    assert RegisteredProject.model_validate(project.model_dump()) == project


def test_registry_entry_round_trips_through_model_dump() -> None:
    entry = _make_entry("no_factory_dir")
    dumped = entry.model_dump()
    assert dumped["condition"] == "no_factory_dir"
    assert RegistryEntry.model_validate(dumped) == entry


# --------------------------------------------------------------------------- #
# Exhaustiveness of the condition union
# --------------------------------------------------------------------------- #


def test_registry_entry_condition_members_are_exhaustive_and_ordered() -> None:
    # Most-degraded-first precedence is the documented resolution rule, and the
    # SPA's generated label map is exhaustive over exactly these members — so a
    # sixth condition must fail here rather than ship silently.
    assert get_args(RegistryEntryCondition) == (
        "ok",
        "path_missing",
        "not_a_project",
        "unreadable",
        "no_factory_dir",
    )


@pytest.mark.parametrize("condition", get_args(RegistryEntryCondition))
def test_every_condition_member_is_accepted_on_the_entry(condition: str) -> None:
    assert RegistryEntry(project=_make_registered_project(), condition=condition).condition == (
        condition
    )
