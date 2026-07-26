"""Hypothesis property tests pinning the flagship v2 write-safety invariant.

Two generative properties hammer the two guarantees example-based tests only
sample:

* **Property A — the mutability gate.** Over a randomized ``RunState`` (the full
  enum) and mutation kind (edit/delete), an in-memory :class:`FakeFileWriter`
  refuses every non-mutable state (``in-flight``/``ready``/``merged``) with
  :class:`TicketNotMutable` and performs ZERO observable mutation of its seeded
  state, while a mutable state (``todo``/``unknown``) applies — so the gate is
  never vacuously rejecting everything.
* **Property B — the co-writer's honest atomicity.** Over a randomized failure
  point in the fixed manifest -> ``.md`` -> roadmap apply sequence, a disk-backed
  :class:`RealFileWriter` surfaces :class:`AtomicWriteError`, never leaves a
  dangling temp file, and never leaves the file that FAILED half-written. A
  failure at the FIRST swap (the manifest) leaves the whole project tree
  byte-identical; a later failure does NOT (the manifest already landed) — the
  ``atomic_write`` module gives PER-FILE atomicity, not multi-file
  transactionality, so this module only asserts the guarantees that actually
  hold. The gate-refused path IS always byte-identical (the gate runs before any
  write), which is the flagship "no write mutates a non-todo ticket" invariant.

The generative counterparts of :mod:`tests.integration.test_real_writer_roundtrip`
(the seeded fuzz) and :mod:`tests.unit.test_atomic_write` (the ``flaky_replace``
injection). Deterministic, hermetic (``tmp_path`` + fixture copies +
``FakeFileWriter`` only), and fast (modest ``max_examples``).
"""

import copy
import hashlib
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from factory_console.domain import Project, RunState
from factory_console.domain.write import TicketDraft, TicketEdit, WriteResult
from factory_console.file_adapter import atomic_write, write_render
from factory_console.file_adapter.atomic_write import AtomicWriteError
from factory_console.file_adapter.fake_writer import FakeFileWriter
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter
from factory_console.file_adapter.write_gate import MUTABLE_STATES, TicketNotMutable

WITH_RUN_STATE = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "with_run_state"

# The fixture's non-mutable tickets (their .md must NEVER change) and its editable
# todo tickets — see the fixture ROADMAP "Run-state note".
_NON_MUTABLE = {
    "CAD-125": RunState.in_flight,
    "CAD-118": RunState.ready,
    "CAD-100": RunState.merged,
}
_TODO_IDS = ["CAD-131", "CAD-140", "CAD-152"]

# Safe ticket-id strategy: always valid against TICKET_ID_PATTERN (r"^[A-Za-z0-9_.-]+$")
# AND, because it starts with a letter, never the bare "." / ".." traversal ids.
_TICKET_IDS = st.from_regex(r"[A-Za-z][A-Za-z0-9_.-]{0,11}", fullmatch=True)
_RUN_STATES = st.sampled_from(list(RunState))
_MUTATION_KINDS = st.sampled_from(["edit", "delete"])


# --------------------------------------------------------------------------- #
# Property A — the mutability gate (in-memory FakeFileWriter, no filesystem)
# --------------------------------------------------------------------------- #


def _mem_project(root: Path = Path("/proj")) -> Project:
    """A Project over in-memory-only paths (they need NOT exist on disk)."""
    return Project(
        rootPath=root,
        ticketsManifestPath=root / "docs" / "planning" / "tickets.json",
        ticketsDir=root / "docs" / "planning" / "tickets",
        roadmapPath=root / "ROADMAP.md",
        runStateDir=root / ".factory" / "run-state",
        discoveredAt=datetime(2026, 7, 20, 12, 0, 0),
    )


def _entry(ticket_id: str) -> dict[str, Any]:
    return {
        "id": ticket_id,
        "title": f"Ticket {ticket_id}",
        "status": "todo",
        "track": "file-adapter",
        "milestone": "MVP",
        "dependsOn": [],
        "provides": f"provides {ticket_id}",
        "files": [],
    }


def _mem_edit() -> TicketEdit:
    return TicketEdit(
        title="Property retitled",
        track="backend",
        milestone="MVP",
        dependsOn=[],
        provides="Property revised",
        files=[],
        bodyMarkdown="# Property retitled\n\nNew body.\n",
    )


def _seeded_fake(ticket_id: str, state: RunState) -> FakeFileWriter:
    """A FakeFileWriter where ``ticket_id`` EXISTS (existence passes) in ``state``.

    A roadmap referencing the ticket under ``## MVP`` is seeded so a successful
    mutation would rewrite it too — strengthening the "zero mutation" assertion on
    the refused path.
    """
    return FakeFileWriter(
        manifest=[_entry(ticket_id)],
        bodies={ticket_id: f"# {ticket_id} body\n"},
        roadmap=f"# Roadmap\n\n## MVP\n\n- [ ] Ticket {ticket_id} ({ticket_id})\n",
        run_states={ticket_id: state},
    )


def _internal_state(writer: FakeFileWriter) -> Any:
    """Deep-copy the writer's mutable seeded state for byte/value-identical compare."""
    return copy.deepcopy((writer._manifest, writer._bodies, writer._front_matter, writer._roadmap))


@settings(max_examples=75)
@given(ticket_id=_TICKET_IDS, state=_RUN_STATES, kind=_MUTATION_KINDS)
def test_gate_refuses_non_mutable_state_with_zero_mutation(
    ticket_id: str, state: RunState, kind: str
) -> None:
    writer = _seeded_fake(ticket_id, state)
    project = _mem_project()

    def op() -> WriteResult:
        if kind == "edit":
            return writer.edit_ticket(project, ticket_id, _mem_edit())
        return writer.delete_ticket(project, ticket_id)

    if state not in MUTABLE_STATES:
        before = _internal_state(writer)
        with pytest.raises(TicketNotMutable) as exc_info:
            op()
        assert exc_info.value.status == 409
        assert exc_info.value.details == {"ticketId": ticket_id, "runState": state.value}
        # ZERO mutating calls: every seeded collection is value-identical afterwards.
        assert _internal_state(writer) == before
    else:
        # Mutable (todo/unknown): the op applies — the gate is not vacuously refusing.
        result = op()
        assert isinstance(result, WriteResult)
        assert result.applied is True
        if kind == "edit":
            assert writer._bodies[ticket_id] == "# Property retitled\n\nNew body.\n"
        else:
            assert ticket_id not in {entry["id"] for entry in writer._manifest}


# --------------------------------------------------------------------------- #
# Property B — the co-writer's honest atomicity (disk-backed RealFileWriter)
# --------------------------------------------------------------------------- #


def _disk_settings(max_examples: int) -> Any:
    """Hypothesis settings for the disk-backed tests: a fresh fixture copy per example
    is made in-body, so the reused function-scoped ``tmp_path`` is safe to suppress."""
    return settings(
        max_examples=max_examples,
        deadline=None,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )


def _fresh_project(tmp_path: Path) -> tuple[RealFileWriter, RealFileAdapter, Project]:
    """Copy the fixture into a UNIQUE subdir of ``tmp_path`` (safe across examples)."""
    root = tmp_path / uuid.uuid4().hex / "project"
    shutil.copytree(WITH_RUN_STATE, root)
    adapter = RealFileAdapter()
    return RealFileWriter(), adapter, adapter.load_project(root)


def _hash_tree(root: Path) -> dict[str, str]:
    """Map every file under ``root`` (project-relative POSIX) to its content SHA-256."""
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _leftover_temps(root: Path) -> list[Path]:
    """Every leftover ``mkstemp`` ``.tmp`` file anywhere under ``root``."""
    return [path for path in root.rglob("*") if path.is_file() and path.name.endswith(".tmp")]


def _fixture_edit() -> TicketEdit:
    return TicketEdit(
        title="Property-fuzzed title",
        track="backend",
        milestone="v2",
        dependsOn=[],
        provides="Property-fuzzed provides",
        files=["server/cadence/fuzz/prop.py"],
        bodyMarkdown="# Property-fuzzed\n\nNew body.\n",
    )


def _flaky_replace(fail_at: int) -> "mock._patch":
    """Patch ``atomic_write.os.replace`` to raise ``OSError`` on its ``fail_at``-th call."""
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: object, dst: object) -> None:
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise OSError("simulated replace failure")
        real_replace(src, dst)

    return mock.patch.object(atomic_write.os, "replace", flaky)


@_disk_settings(40)
@given(item=st.sampled_from(list(_NON_MUTABLE.items())), kind=_MUTATION_KINDS)
def test_gate_refusal_on_disk_leaves_whole_tree_byte_identical(
    tmp_path: Path, item: tuple[str, RunState], kind: str
) -> None:
    # The flagship invariant: an edit/delete on a non-mutable ticket is refused by
    # the gate BEFORE any write, so the entire project tree is byte-identical.
    ticket_id, state = item
    writer, _adapter, project = _fresh_project(tmp_path)
    before = _hash_tree(project.rootPath)

    with pytest.raises(TicketNotMutable) as exc_info:
        if kind == "edit":
            writer.edit_ticket(project, ticket_id, _fixture_edit())
        else:
            writer.delete_ticket(project, ticket_id)

    assert exc_info.value.status == 409
    assert exc_info.value.details == {"ticketId": ticket_id, "runState": state.value}
    assert _hash_tree(project.rootPath) == before
    assert _leftover_temps(project.rootPath) == []


@_disk_settings(40)
@given(ticket_id=st.sampled_from(_TODO_IDS), data=st.data())
def test_mid_apply_failure_never_leaves_a_half_written_or_dangling_file(
    tmp_path: Path, ticket_id: str, data: st.DataObject
) -> None:
    writer, _adapter, project = _fresh_project(tmp_path)
    edit = _fixture_edit()

    # Every planned change of an edit is a swap (never a delete), so the number of
    # os.replace calls equals the number of planned files: manifest -> .md [-> roadmap].
    planned = write_render.render_edit(project, ticket_id, edit)
    fail_at = data.draw(st.integers(min_value=1, max_value=len(planned)), label="fail_at")
    before = _hash_tree(project.rootPath)

    with _flaky_replace(fail_at), pytest.raises(AtomicWriteError) as exc_info:
        writer.edit_ticket(project, ticket_id, edit)

    assert exc_info.value.status == 500
    assert isinstance(exc_info.value.__cause__, OSError)
    # Guarantees that ALWAYS hold: no dangling temp, and the file that failed keeps
    # its pre-existing bytes (per-file atomicity — os.replace is all-or-nothing).
    assert _leftover_temps(project.rootPath) == []
    failed_rel = exc_info.value.details["relPath"]
    after = _hash_tree(project.rootPath)
    assert after[failed_rel] == before[failed_rel]

    if fail_at == 1:
        # First swap is the manifest (rank 0): the WHOLE trio is byte-for-byte unchanged.
        assert after == before
    else:
        # A later failure is NOT whole-trio-unchanged — the manifest already landed.
        assert after["docs/planning/tickets.json"] != before["docs/planning/tickets.json"]


@_disk_settings(25)
@given(new_id=st.from_regex(r"NEW-[A-Za-z0-9]{1,6}", fullmatch=True))
def test_create_md_swap_failure_leaves_the_new_md_absent(tmp_path: Path, new_id: str) -> None:
    # The "absent if it was a create" half of the no-half-written guarantee: a create
    # failing at the .md swap (call #2, after the manifest) leaves no .md on disk.
    writer, _adapter, project = _fresh_project(tmp_path)
    draft = TicketDraft(
        id=new_id,
        title="Property create",
        track="backend",
        milestone="v2",
        dependsOn=["CAD-152"],
        provides="Property created ticket",
        files=["server/cadence/fuzz/create.py"],
        bodyMarkdown="# Property create\n\nBody.\n",
    )
    md_path = project.ticketsDir / f"{new_id}.md"
    assert not md_path.exists()

    with _flaky_replace(2), pytest.raises(AtomicWriteError) as exc_info:
        writer.create_ticket(project, draft)

    assert exc_info.value.details["relPath"] == f"docs/planning/tickets/{new_id}.md"
    assert not md_path.exists()  # never half-written
    assert _leftover_temps(project.rootPath) == []


@_disk_settings(25)
@given(ticket_id=st.sampled_from(_TODO_IDS))
def test_successful_edit_is_all_or_nothing_and_rereads(tmp_path: Path, ticket_id: str) -> None:
    # The success case: an edit on a todo ticket updates the coupled files
    # consistently, re-reads correctly through the production adapter, and leaves no
    # dangling temp.
    writer, adapter, project = _fresh_project(tmp_path)

    result = writer.edit_ticket(project, ticket_id, _fixture_edit())

    assert result.applied is True
    assert result.ticket is not None
    assert result.ticket.title == "Property-fuzzed title"
    reread = adapter.get_ticket(project, ticket_id)
    assert reread is not None
    assert reread.title == "Property-fuzzed title"
    assert reread.bodyMarkdown == "# Property-fuzzed\n\nNew body.\n"
    assert _leftover_temps(project.rootPath) == []
