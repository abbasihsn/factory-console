"""Integration + property-style tests for :class:`RealFileWriter`.

End-to-end over a tmp copy of the ``with_run_state`` fixture: a create/delete
round-trip verified through the production
:class:`~factory_console.file_adapter.real.RealFileAdapter` (list/read/get_deps),
plus a seeded property-style fuzz that hammers the writer with a random sequence
of edit/delete attempts and asserts the two write-path safety invariants after
EVERY operation:

* no non-todo (``in-flight``/``ready``/``merged``) ticket's ``.md`` is ever
  mutated, and
* the entire ``.factory/run-state`` tree is byte-identical.

The fuzz uses stdlib :class:`random.Random` with a FIXED seed so it is fully
deterministic and reproducible — no ``hypothesis`` dependency.
"""

import hashlib
import random
import shutil
from pathlib import Path

from factory_console.domain import Project
from factory_console.domain.write import TicketDraft, TicketEdit
from factory_console.file_adapter.path_safety import PathTraversal
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter
from factory_console.file_adapter.write_gate import TicketNotMutable
from factory_console.file_adapter.write_render import UnknownTicket

WITH_RUN_STATE = Path(__file__).resolve().parents[1] / "fixtures" / "projects" / "with_run_state"

# The fixture's non-mutable tickets — their .md files must NEVER change.
_NON_MUTABLE_IDS = ["CAD-100", "CAD-118", "CAD-125"]
_TODO_IDS = ["CAD-131", "CAD-140", "CAD-152"]
_UNKNOWN_IDS = ["CAD-900", "CAD-901"]


def _load(tmp_path: Path) -> tuple[RealFileWriter, RealFileAdapter, Project]:
    """Copy the fixture into ``tmp_path`` and return writer + adapter + project."""
    root = tmp_path / "project"
    shutil.copytree(WITH_RUN_STATE, root)
    adapter = RealFileAdapter()
    return RealFileWriter(), adapter, adapter.load_project(root)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_dir(root: Path) -> dict[str, str]:
    """Map every file under ``root`` (root-relative POSIX) to its content SHA-256."""
    return {
        path.relative_to(root).as_posix(): _hash_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _non_mutable_md_hashes(project: Project) -> dict[str, str]:
    return {
        ticket_id: _hash_file(project.ticketsDir / f"{ticket_id}.md")
        for ticket_id in _NON_MUTABLE_IDS
    }


# --------------------------------------------------------------------------- #
# create → read round-trip
# --------------------------------------------------------------------------- #


def test_create_then_read_deps_roundtrip(tmp_path: Path) -> None:
    writer, adapter, project = _load(tmp_path)

    draft = TicketDraft(
        id="CAD-210",
        title="Team analytics dashboard",
        track="frontend",
        milestone="v2",
        dependsOn=["CAD-152"],
        provides="Participation and consistency across a team",
        files=["frontend/src/routes/team/+page.svelte"],
        bodyMarkdown="# Team analytics\n\nDashboard body.\n",
    )
    result = writer.create_ticket(project, draft)
    assert result.applied is True

    # The production read adapter sees the new ticket end-to-end.
    assert "CAD-210" in {s.id for s in adapter.list_tickets(project)}
    ticket = adapter.get_ticket(project, "CAD-210")
    assert ticket is not None
    assert ticket.title == "Team analytics dashboard"

    deps = adapter.get_deps(project, "CAD-210")
    assert deps is not None
    assert [dep.id for dep in deps.directDeps] == ["CAD-152"]


def test_delete_todo_ticket_then_no_longer_listed(tmp_path: Path) -> None:
    writer, adapter, project = _load(tmp_path)

    assert "CAD-152" in {s.id for s in adapter.list_tickets(project)}
    writer.delete_ticket(project, "CAD-152")
    assert "CAD-152" not in {s.id for s in adapter.list_tickets(project)}
    assert adapter.get_ticket(project, "CAD-152") is None


# --------------------------------------------------------------------------- #
# property-style fuzz — seeded, deterministic, invariant-checked every op
# --------------------------------------------------------------------------- #


def _random_edit(rng: random.Random, ticket_id: str, step: int) -> TicketEdit:
    """Build a deterministic, varied edit for ``ticket_id`` from the seeded rng."""
    return TicketEdit(
        title=f"{ticket_id} fuzzed {step}",
        track=rng.choice(["backend", "frontend", "api", "data"]),
        milestone=rng.choice(["MVP", "v1", "v2", None]),
        dependsOn=rng.sample(_TODO_IDS + _NON_MUTABLE_IDS, k=rng.randint(0, 2)),
        provides=f"provides after step {step}",
        files=[f"server/cadence/fuzz/{ticket_id}_{step}.py"],
        bodyMarkdown=f"# {ticket_id}\n\nFuzzed body {step}.\n",
    )


def test_random_edits_never_mutate_non_todo_or_run_state(tmp_path: Path) -> None:
    writer, adapter, project = _load(tmp_path)
    run_state_dir = project.runStateDir
    assert run_state_dir is not None

    # Baselines that must survive EVERY operation, mutating or refused.
    baseline_non_mutable = _non_mutable_md_hashes(project)
    baseline_run_state = _hash_dir(run_state_dir)

    rng = random.Random(1234)
    pool = _TODO_IDS + _NON_MUTABLE_IDS + _UNKNOWN_IDS
    applied = 0
    refused = 0

    for step in range(50):
        ticket_id = rng.choice(pool)
        op = rng.choice(["edit", "delete"])
        try:
            if op == "edit":
                writer.edit_ticket(project, ticket_id, _random_edit(rng, ticket_id, step))
            else:
                writer.delete_ticket(project, ticket_id)
            applied += 1
        except (TicketNotMutable, UnknownTicket, PathTraversal):
            # Expected: non-todo → 409, already-deleted/unknown → 404. Never fatal.
            refused += 1

        # Invariant 1: no non-todo ticket's .md ever changes (present + byte-identical).
        assert _non_mutable_md_hashes(project) == baseline_non_mutable
        # Invariant 2: the run-state tree is byte-identical, always.
        assert _hash_dir(run_state_dir) == baseline_run_state

    # The seeded run exercised BOTH a real mutating path and the refusal path, so
    # the invariants are meaningfully tested (not vacuously green on all-refusals).
    assert applied > 0
    assert refused > 0
